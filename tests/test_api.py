"""Tests for the Cartesia API client.

Runs against fakes, never against the real API. Only needs ``aiohttp`` and
``pytest``; Home Assistant is not imported.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from typing import Any

import aiohttp
import pytest
from cartesia_tts import api
from cartesia_tts.api import (
    CartesiaAuthError,
    CartesiaClient,
    CartesiaConnectionError,
    CartesiaError,
    build_generation_config,
    normalize_emotion,
    normalize_speed,
    wav_header,
)


def run(coro: Any) -> Any:
    """Run a coroutine without pulling in pytest-asyncio."""
    return asyncio.run(coro)


# --------------------------------------------------------------------- fakes --
class FakeResponse:
    """Stand-in for an aiohttp response context manager."""

    def __init__(self, status: int, payload: Any = None, body: bytes = b"") -> None:
        self.status = status
        self._payload = payload
        self._body = body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def json(self) -> Any:
        return self._payload

    async def read(self) -> bytes:
        return self._body

    async def text(self) -> str:
        return "error detail"


class FakeSession:
    """Replays queued responses and records the requests that were made."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, Any, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs.get("params"), kwargs.get("headers")))
        return self._responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs.get("json"), kwargs.get("headers")))
        return self._responses.pop(0)


class FakeMessage:
    """A single WebSocket frame."""

    def __init__(self, type_: aiohttp.WSMsgType, data: Any) -> None:
        self.type = type_
        self.data = data


class FakeWebSocket:
    """Records outgoing frames and replays queued incoming ones."""

    def __init__(self, frames: list[FakeMessage] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._frames = list(frames or [])

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def receive(self, timeout: float | None = None) -> FakeMessage:
        if not self._frames:
            return FakeMessage(aiohttp.WSMsgType.CLOSED, None)
        return self._frames.pop(0)

    def exception(self) -> Exception | None:
        return None


def voice(voice_id: str, language: str = "de") -> dict[str, Any]:
    """Build a minimal voice record."""
    return {"id": voice_id, "name": f"Voice {voice_id}", "language": language}


async def text_stream(*chunks: str):
    """Yield transcript chunks like Home Assistant's message generator does."""
    for chunk in chunks:
        yield chunk


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry tests fast."""
    monkeypatch.setattr(api, "RETRY_BACKOFF", 0)


# ------------------------------------------------------------------- speed ---
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("slowest", 0.6),
        ("slow", 0.8),
        ("Normal", 1.0),
        ("fast", 1.2),
        ("fastest", 1.5),
        (1.25, 1.25),
        ("0.9", 0.9),
        (3.0, 1.5),  # clamped
        (0.1, 0.6),  # clamped
        (None, None),
        ("", None),
        ("schnell", None),
        (True, None),  # bools are not speeds
    ],
)
def test_normalize_speed(value: Any, expected: float | None) -> None:
    assert normalize_speed(value) == expected


# ----------------------------------------------------------------- emotion ---
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("angry", "angry"),
        (" Calm ", "calm"),
        ("curious:high", "curious"),  # legacy tag form
        ("positivity:high", None),  # no longer a Cartesia emotion
        (["nope", "sad"], "sad"),
        ("hangry", None),
        (None, None),
    ],
)
def test_normalize_emotion(value: Any, expected: str | None) -> None:
    assert normalize_emotion(value) == expected


# -------------------------------------------------------- generation config --
def test_generation_config_combines_speed_and_emotion() -> None:
    assert build_generation_config("fast", "angry", "sonic-3.5") == {
        "speed": 1.2,
        "emotion": "angry",
    }


def test_generation_config_omits_unset_values() -> None:
    assert build_generation_config(1.0, None, "sonic-3") == {"speed": 1.0}
    assert build_generation_config(None, None, "sonic-3.5") is None


def test_generation_config_dropped_on_unsupported_model() -> None:
    assert build_generation_config("fast", "angry", "sonic-latest") is None


# -------------------------------------------------------------- wav header ---
def test_wav_header() -> None:
    header = wav_header(44100)
    assert len(header) == 44
    assert header[:4] == b"RIFF"
    assert header[8:12] == b"WAVE"
    assert header[36:40] == b"data"
    assert struct.unpack("<I", header[24:28])[0] == 44100
    assert struct.unpack("<I", header[28:32])[0] == 44100 * 2  # 16 bit mono
    assert struct.unpack("<H", header[34:36])[0] == 16


# ------------------------------------------------------------------ voices ---
def test_list_voices_follows_pagination() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {"data": [voice("a"), voice("b")], "has_more": True, "next_page": "b"},
            ),
            FakeResponse(200, {"data": [voice("c")], "has_more": False}),
        ]
    )
    voices = run(CartesiaClient(session, "sk_car_test").list_voices())

    assert [v["id"] for v in voices] == ["a", "b", "c"]
    assert session.calls[1][2]["starting_after"] == "b"


def test_rest_headers() -> None:
    session = FakeSession([FakeResponse(200, {"data": [], "has_more": False})])
    run(CartesiaClient(session, "sk_car_test").list_voices())

    headers = session.calls[0][3]
    assert headers["Authorization"] == "Bearer sk_car_test"
    assert headers["Cartesia-Version"] == api.CARTESIA_VERSION


# ------------------------------------------------------------------ errors ---
def test_retries_once_on_server_error() -> None:
    session = FakeSession(
        [
            FakeResponse(503),
            FakeResponse(200, {"data": [voice("a")], "has_more": False}),
        ]
    )
    assert [v["id"] for v in run(CartesiaClient(session, "k").list_voices())] == ["a"]


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        ([FakeResponse(401)], CartesiaAuthError),
        ([FakeResponse(403)], CartesiaAuthError),
        ([FakeResponse(500), FakeResponse(500)], CartesiaConnectionError),
        ([FakeResponse(429), FakeResponse(429)], CartesiaConnectionError),
        ([FakeResponse(400)], CartesiaError),
    ],
)
def test_http_error_mapping(
    responses: list[FakeResponse], expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        run(CartesiaClient(FakeSession(responses), "k").list_voices())


# -------------------------------------------------------------- tts/bytes ---
def test_synthesize_bytes_request_body() -> None:
    session = FakeSession([FakeResponse(200, body=b"ID3fake")])
    audio = run(
        CartesiaClient(session, "k").synthesize_bytes(
            model="sonic-3.5",
            transcript="Hallo Welt.",
            voice_id="v1",
            language="de",
            generation_config={"speed": 1.2},
        )
    )

    assert audio == b"ID3fake"
    body = session.calls[0][2]
    assert body["model_id"] == "sonic-3.5"
    assert body["transcript"] == "Hallo Welt."
    assert body["voice"] == {"mode": "id", "id": "v1"}
    assert body["language"] == "de"
    assert body["output_format"] == {
        "container": "mp3",
        "sample_rate": 44100,
        "bit_rate": 128000,
    }
    assert body["generation_config"] == {"speed": 1.2}


def test_synthesize_bytes_omits_optional_fields() -> None:
    session = FakeSession([FakeResponse(200, body=b"")])
    run(
        CartesiaClient(session, "k").synthesize_bytes(
            model="sonic-3.5",
            transcript="Hi",
            voice_id="v1",
            language=None,
        )
    )
    body = session.calls[0][2]
    assert "language" not in body
    assert "generation_config" not in body


# --------------------------------------------------------------- websocket ---
def test_send_transcript_marks_last_chunk_as_final() -> None:
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket,
            {"model_id": "sonic-3.5"},
            "ctx",
            text_stream("Hallo, ", "wie geht es dir?"),
        )
    )

    assert [frame["continue"] for frame in websocket.sent] == [True, False]
    assert [frame["transcript"] for frame in websocket.sent] == [
        "Hallo, ",
        "wie geht es dir?",
    ]
    assert {frame["context_id"] for frame in websocket.sent} == {"ctx"}
    assert websocket.sent[0]["max_buffer_delay_ms"] == api.MAX_BUFFER_DELAY_MS


def test_send_transcript_single_chunk() -> None:
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket, {}, "ctx", text_stream("Nur ein Satz.")
        )
    )
    assert len(websocket.sent) == 1
    assert websocket.sent[0]["continue"] is False


def test_send_transcript_closes_empty_context() -> None:
    websocket = FakeWebSocket()
    run(
        CartesiaClient(FakeSession([]), "k")._send_transcript(
            websocket, {}, "ctx", text_stream()
        )
    )
    assert websocket.sent[0]["continue"] is False


def _frame(payload: dict[str, Any]) -> FakeMessage:
    return FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload))


def _collect(frames: list[FakeMessage]) -> bytes:
    client = CartesiaClient(FakeSession([]), "k")

    async def drain() -> bytes:
        chunks = [
            chunk async for chunk in client._receive_audio(FakeWebSocket(frames), "ctx")
        ]
        return b"".join(chunks)

    return run(drain())


def test_receive_audio_assembles_chunks() -> None:
    audio = _collect(
        [
            _frame(
                {
                    "type": "chunk",
                    "context_id": "ctx",
                    "data": base64.b64encode(b"\x01\x02").decode(),
                }
            ),
            _frame({"type": "timestamps", "context_id": "ctx"}),
            _frame(
                {
                    "type": "chunk",
                    "context_id": "ctx",
                    "data": base64.b64encode(b"\x03").decode(),
                }
            ),
            _frame({"type": "done", "context_id": "ctx"}),
        ]
    )
    assert audio == b"\x01\x02\x03"


def test_receive_audio_raises_on_error_frame() -> None:
    with pytest.raises(CartesiaError):
        _collect(
            [
                _frame(
                    {
                        "type": "error",
                        "context_id": "ctx",
                        "message": "bad voice",
                        "status_code": 400,
                    }
                )
            ]
        )


def test_receive_audio_raises_auth_error_on_401_frame() -> None:
    with pytest.raises(CartesiaAuthError):
        _collect(
            [
                _frame(
                    {
                        "type": "error",
                        "context_id": "ctx",
                        "message": "nope",
                        "status_code": 401,
                    }
                )
            ]
        )


def test_receive_audio_raises_on_premature_close() -> None:
    with pytest.raises(CartesiaConnectionError):
        _collect([])
