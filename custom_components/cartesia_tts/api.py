"""Thin aiohttp client for the Cartesia TTS API.

This module owns everything Cartesia-specific: URLs, headers, request/response
shapes and the speed/emotion translation. It deliberately knows nothing about
Home Assistant, so an API version bump only touches this file.

Schema reference (verified 2026-07 against docs.cartesia.ai):
  * REST auth   : ``Authorization: Bearer <key>`` + ``Cartesia-Version`` header
  * WebSocket   : ``X-API-Key`` header + ``cartesia_version`` query parameter
  * GET /voices : paginated ``{"data": [...], "has_more": bool, "next_page": str}``
  * POST /tts/bytes    : mp3/wav/raw output
  * WSS /tts/websocket : raw PCM only
  * speed/emotion      : ``generation_config`` (not ``experimental_controls``)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import struct
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    CARTESIA_VERSION,
    EMOTIONS,
    MAX_BUFFER_DELAY_MS,
    MODELS_WITH_GENERATION_CONFIG,
    MP3_BIT_RATE,
    MP3_SAMPLE_RATE,
    PCM_ENCODING,
    PCM_SAMPLE_RATE,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF,
    SPEED_KEYWORDS,
    SPEED_MAX,
    SPEED_MIN,
    VOICES_MAX_PAGES,
    VOICES_PAGE_SIZE,
    WS_BASE,
    WS_RECEIVE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class CartesiaError(Exception):
    """Base error for the Cartesia API."""


class CartesiaAuthError(CartesiaError):
    """The API key was rejected."""


class CartesiaConnectionError(CartesiaError):
    """Cartesia was unreachable or kept failing."""


MP3_OUTPUT_FORMAT: dict[str, Any] = {
    "container": "mp3",
    "sample_rate": MP3_SAMPLE_RATE,
    "bit_rate": MP3_BIT_RATE,
}
PCM_OUTPUT_FORMAT: dict[str, Any] = {
    "container": "raw",
    "encoding": PCM_ENCODING,
    "sample_rate": PCM_SAMPLE_RATE,
}


def wav_header(
    sample_rate: int = PCM_SAMPLE_RATE, channels: int = 1, bits: int = 16
) -> bytes:
    """Return a WAV header for a stream of unknown length.

    Cartesia's WebSocket only emits raw PCM, but Home Assistant hands the stream
    to media players that expect a container. The size fields are left at
    0xFFFFFFFF, the usual convention for "read until the connection closes".
    """
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        0xFFFFFFFF,
        b"WAVE",
        b"fmt ",
        16,  # PCM chunk size
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        0xFFFFFFFF,
    )


def normalize_speed(speed: Any) -> float | None:
    """Map a keyword or number onto Cartesia's speed range, or None if unset."""
    if speed is None or speed == "":
        return None
    if isinstance(speed, str):
        key = speed.strip().lower()
        if key in SPEED_KEYWORDS:
            return SPEED_KEYWORDS[key]
        try:
            value = float(key)
        except ValueError:
            _LOGGER.warning(
                "Ignoring unknown speed %r, expected a number or one of %s",
                speed,
                ", ".join(SPEED_KEYWORDS),
            )
            return None
    elif isinstance(speed, (int, float)) and not isinstance(speed, bool):
        value = float(speed)
    else:
        _LOGGER.warning("Ignoring unsupported speed value %r", speed)
        return None

    clamped = min(max(value, SPEED_MIN), SPEED_MAX)
    if clamped != value:
        _LOGGER.debug("Clamped speed %s to %s", value, clamped)
    return clamped


def normalize_emotion(emotion: Any) -> str | None:
    """Map an emotion option onto Cartesia's enum, or None if unusable.

    Accepts a plain name, a list (first usable entry wins) and the legacy
    ``name:level`` tag form of the old experimental_controls API.
    """
    if emotion is None or emotion == "":
        return None
    if isinstance(emotion, (list, tuple, set)):
        for candidate in emotion:
            if (resolved := normalize_emotion(candidate)) is not None:
                return resolved
        return None
    if not isinstance(emotion, str):
        _LOGGER.warning("Ignoring unsupported emotion value %r", emotion)
        return None

    # "positivity:high" -> "positivity"; levels are no longer part of the API.
    name = emotion.strip().lower().split(":", 1)[0]
    if name in EMOTIONS:
        return name
    _LOGGER.warning(
        "Ignoring unknown emotion %r; Cartesia accepts one of: %s",
        emotion,
        ", ".join(EMOTIONS),
    )
    return None


def build_generation_config(
    speed: Any, emotion: Any, model: str
) -> dict[str, Any] | None:
    """Build the ``generation_config`` payload, or None when nothing applies."""
    normalized_speed = normalize_speed(speed)
    normalized_emotion = normalize_emotion(emotion)
    if normalized_speed is None and normalized_emotion is None:
        return None
    if model not in MODELS_WITH_GENERATION_CONFIG:
        _LOGGER.debug(
            "Model %s does not support speed/emotion controls, dropping them", model
        )
        return None

    config: dict[str, Any] = {}
    if normalized_speed is not None:
        config["speed"] = normalized_speed
    if normalized_emotion is not None:
        config["emotion"] = normalized_emotion
    return config


class CartesiaClient:
    """Minimal async client covering the endpoints this integration needs."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Initialize the client with a shared Home Assistant session."""
        self._session = session
        self._api_key = api_key

    @property
    def _rest_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Cartesia-Version": CARTESIA_VERSION,
        }

    async def list_voices(self) -> list[dict[str, Any]]:
        """Return every voice available to this key, following pagination."""
        voices: list[dict[str, Any]] = []
        starting_after: str | None = None

        for _ in range(VOICES_MAX_PAGES):
            params: dict[str, Any] = {"limit": VOICES_PAGE_SIZE}
            if starting_after:
                params["starting_after"] = starting_after

            payload = await self._get_json("/voices", params)
            if isinstance(payload, list):  # defensive: older non-paginated shape
                voices.extend(payload)
                break

            page = payload.get("data") or []
            voices.extend(page)
            if not payload.get("has_more") or not page:
                break
            starting_after = payload.get("next_page") or page[-1].get("id")
            if not starting_after:
                break
        else:
            _LOGGER.warning(
                "Stopped paginating /voices after %s pages", VOICES_MAX_PAGES
            )

        _LOGGER.debug("Loaded %s Cartesia voices", len(voices))
        return voices

    async def synthesize_bytes(
        self,
        *,
        model: str,
        transcript: str,
        voice_id: str,
        language: str | None,
        generation_config: dict[str, Any] | None = None,
    ) -> bytes:
        """Synthesize the full transcript and return MP3 bytes."""
        body: dict[str, Any] = {
            "model_id": model,
            "transcript": transcript,
            "voice": {"mode": "id", "id": voice_id},
            "output_format": MP3_OUTPUT_FORMAT,
        }
        if language:
            body["language"] = language
        if generation_config:
            body["generation_config"] = generation_config

        return await self._post_bytes("/tts/bytes", body)

    async def synthesize_stream(
        self,
        *,
        model: str,
        transcript_gen: AsyncIterable[str],
        voice_id: str,
        language: str | None,
        generation_config: dict[str, Any] | None = None,
    ) -> AsyncGenerator[bytes]:
        """Stream raw PCM while the transcript is still being produced.

        Text chunks are forwarded to Cartesia as continuations of one context,
        so audio starts before the full message exists.
        """
        base: dict[str, Any] = {
            "model_id": model,
            "voice": {"mode": "id", "id": voice_id},
            "output_format": PCM_OUTPUT_FORMAT,
        }
        if language:
            base["language"] = language
        if generation_config:
            base["generation_config"] = generation_config

        context_id = uuid.uuid4().hex
        url = f"{WS_BASE}?cartesia_version={CARTESIA_VERSION}"

        try:
            async with self._session.ws_connect(
                url, headers={"X-API-Key": self._api_key}, heartbeat=20
            ) as websocket:
                sender = asyncio.create_task(
                    self._send_transcript(websocket, base, context_id, transcript_gen)
                )
                try:
                    async for chunk in self._receive_audio(websocket, context_id):
                        yield chunk
                    # Surface send failures that the receive loop did not notice.
                    await sender
                except BaseException:
                    # Covers errors as well as the consumer closing the
                    # generator early; never leave the sender task dangling.
                    sender.cancel()
                    with contextlib.suppress(BaseException):
                        await sender
                    raise
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise CartesiaAuthError("Cartesia rejected the API key") from err
            raise CartesiaConnectionError(
                f"Cartesia WebSocket handshake failed: {err}"
            ) from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise CartesiaConnectionError(
                f"Cartesia WebSocket connection failed: {err}"
            ) from err

    async def _send_transcript(
        self,
        websocket: aiohttp.ClientWebSocketResponse,
        base: dict[str, Any],
        context_id: str,
        transcript_gen: AsyncIterable[str],
    ) -> None:
        """Forward text chunks, marking the last one as the end of the context.

        One chunk is held back so the final message can carry
        ``"continue": false`` without sending an empty transcript.
        """
        pending: str | None = None
        async for chunk in transcript_gen:
            if not chunk:
                continue
            if pending is not None:
                await websocket.send_json(
                    {
                        **base,
                        "context_id": context_id,
                        "transcript": pending,
                        "continue": True,
                        "max_buffer_delay_ms": MAX_BUFFER_DELAY_MS,
                    }
                )
            pending = chunk

        await websocket.send_json(
            {
                **base,
                "context_id": context_id,
                # A lone space closes an empty context without an API error.
                "transcript": pending if pending is not None else " ",
                "continue": False,
            }
        )

    async def _receive_audio(
        self, websocket: aiohttp.ClientWebSocketResponse, context_id: str
    ) -> AsyncGenerator[bytes]:
        """Yield decoded PCM chunks until Cartesia reports the context done."""
        while True:
            message = await websocket.receive(timeout=WS_RECEIVE_TIMEOUT)

            if message.type is aiohttp.WSMsgType.BINARY:
                yield message.data
                continue
            if message.type is not aiohttp.WSMsgType.TEXT:
                if message.type is aiohttp.WSMsgType.ERROR:
                    raise CartesiaConnectionError(
                        f"Cartesia WebSocket error: {websocket.exception()}"
                    )
                # CLOSE/CLOSED/CLOSING: the server hung up without a "done".
                raise CartesiaConnectionError(
                    "Cartesia closed the WebSocket before finishing the audio"
                )

            try:
                payload = json.loads(message.data)
            except ValueError as err:
                raise CartesiaError(f"Malformed Cartesia message: {err}") from err

            if payload.get("context_id") not in (None, context_id):
                continue

            match payload.get("type"):
                case "chunk":
                    if data := payload.get("data"):
                        yield base64.b64decode(data)
                case "done":
                    return
                case "error":
                    detail = payload.get("message") or payload.get("title") or "unknown"
                    if payload.get("status_code") in (401, 403):
                        raise CartesiaAuthError(detail)
                    raise CartesiaError(f"Cartesia stream error: {detail}")
                case _:
                    # timestamps / flush_done and friends are not used here.
                    continue

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        """GET a JSON endpoint with one retry on rate limits and 5xx."""
        url = f"{API_BASE}{path}"
        for attempt in range(2):
            try:
                async with self._session.get(
                    url,
                    headers=self._rest_headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as response:
                    if await self._should_retry(response, attempt, path):
                        continue
                    await self._raise_for_status(response, path)
                    return await response.json()
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt == 0:
                    _LOGGER.debug("Retrying GET %s after %s", path, err)
                    await asyncio.sleep(RETRY_BACKOFF)
                    continue
                raise CartesiaConnectionError(f"GET {path} failed: {err}") from err
        raise CartesiaConnectionError(f"GET {path} failed after retrying")

    async def _post_bytes(self, path: str, body: dict[str, Any]) -> bytes:
        """POST a JSON body and return the raw response payload."""
        url = f"{API_BASE}{path}"
        for attempt in range(2):
            try:
                async with self._session.post(
                    url,
                    headers=self._rest_headers,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as response:
                    if await self._should_retry(response, attempt, path):
                        continue
                    await self._raise_for_status(response, path)
                    return await response.read()
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt == 0:
                    _LOGGER.debug("Retrying POST %s after %s", path, err)
                    await asyncio.sleep(RETRY_BACKOFF)
                    continue
                raise CartesiaConnectionError(f"POST {path} failed: {err}") from err
        raise CartesiaConnectionError(f"POST {path} failed after retrying")

    async def _should_retry(
        self, response: aiohttp.ClientResponse, attempt: int, path: str
    ) -> bool:
        """Return True when the caller should back off and try once more."""
        if response.status != 429 and response.status < 500:
            return False
        if attempt != 0:
            return False
        _LOGGER.debug("Cartesia returned %s for %s, retrying", response.status, path)
        await asyncio.sleep(RETRY_BACKOFF)
        return True

    async def _raise_for_status(
        self, response: aiohttp.ClientResponse, path: str
    ) -> None:
        """Translate an HTTP error into a typed Cartesia exception."""
        if response.status < 400:
            return
        detail = (await response.text())[:200]
        if response.status in (401, 403):
            raise CartesiaAuthError(f"Cartesia rejected the API key: {detail}")
        if response.status == 429 or response.status >= 500:
            raise CartesiaConnectionError(
                f"{path} failed with HTTP {response.status}: {detail}"
            )
        raise CartesiaError(f"{path} failed with HTTP {response.status}: {detail}")
