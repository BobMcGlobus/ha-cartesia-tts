"""Text-to-speech entity for Cartesia Sonic."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, override

from homeassistant.components.tts import (
    ATTR_VOICE,
    TextToSpeechEntity,
    TTSAudioRequest,
    TTSAudioResponse,
    TtsAudioType,
    Voice,
)
from homeassistant.components.tts.const import DATA_COMPONENT
from homeassistant.const import ATTR_MODEL, CONF_MODEL, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import CartesiaConfigEntry
from .api import (
    CartesiaAuthError,
    CartesiaClient,
    CartesiaError,
    CartesiaQuotaError,
    build_generation_config,
    wav_header,
)
from .const import (
    CARTESIA_TO_HA,
    CONF_EMOTION,
    CONF_FALLBACK_ENGINE,
    CONF_LANGUAGE,
    CONF_SPEED,
    CONF_STREAMING,
    CONF_VOICE,
    CONF_VOLUME,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DOMAIN,
    FALLBACK_LANGUAGES,
    HA_TO_CARTESIA,
    ISSUE_QUOTA_EXHAUSTED,
    MANUFACTURER,
    USAGE_URL,
    VOICES_REFRESH_INTERVAL_HOURS,
)
from .usage import UsageTracker

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


class _RecordedMessage:
    """Forwards a message stream while remembering what went through.

    Two things need the text that Home Assistant streams in: Cartesia, chunk by
    chunk, and - if Cartesia fails - the fallback engine, which needs it whole.
    Characters are counted as they are sent, because that is what Cartesia
    bills, whether or not the request ends up succeeding.
    """

    def __init__(self, source: AsyncIterable[str], usage: UsageTracker) -> None:
        """Wrap the message generator Home Assistant handed us."""
        self._source = source
        self._usage = usage
        self.text = ""

    async def stream(self) -> AsyncGenerator[str]:
        """Yield the chunks, recording them on the way past."""
        async for chunk in self._source:
            self._record(chunk)
            yield chunk

    async def drain(self) -> str:
        """Collect whatever is left, for handing a whole message to a fallback."""
        try:
            async for chunk in self._source:
                self._record(chunk)
        except Exception as err:  # noqa: BLE001 - a dead stream must not mask the real error
            _LOGGER.debug("Could not drain the message stream: %s", err)
        return self.text

    def _record(self, chunk: str) -> None:
        self.text += chunk
        self._usage.async_add_characters(len(chunk))


@dataclass(frozen=True, slots=True)
class _Request:
    """Everything a synthesis call needs after options have been merged."""

    model: str
    voice_id: str
    language: str | None
    generation_config: dict[str, Any] | None


def derive_languages(voices: list[dict[str, Any]]) -> list[str]:
    """Map the languages Cartesia actually offers onto HA locale codes.

    Codes that are not in ``CARTESIA_TO_HA`` are exposed verbatim rather than
    dropped, so a newly added Cartesia language still shows up.
    """
    languages: set[str] = set()
    for code in {v.get("language") for v in voices if v.get("language")}:
        if mapped := CARTESIA_TO_HA.get(code):
            languages.update(mapped)
        else:
            _LOGGER.info(
                "Cartesia language %r has no HA locale mapping, exposing it as-is",
                code,
            )
            languages.add(code)
    return sorted(languages)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CartesiaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Cartesia TTS entity."""
    async_add_entities([CartesiaTTSEntity(entry)])


class CartesiaTTSEntity(TextToSpeechEntity):
    """A single entity exposing every Cartesia voice."""

    _attr_has_entity_name = True
    # Home Assistant refuses to synthesize when the entity name resolves to
    # None or UNDEFINED, so this is set explicitly rather than left to the
    # device name. Together with the device name it reads "Cartesia Sonic TTS".
    _attr_name = "Sonic TTS"
    # Core is split on this: ElevenLabs marks its TTS entity as CONFIG, Google
    # Cloud and OpenAI leave it unset. Following ElevenLabs keeps the entity out
    # of the default dashboard, where a TTS engine is noise.
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_options = [
        ATTR_VOICE,
        ATTR_MODEL,
        CONF_SPEED,
        CONF_EMOTION,
        CONF_VOLUME,
    ]

    def __init__(self, entry: CartesiaConfigEntry) -> None:
        """Initialize the entity from the config entry."""
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=entry.options.get(CONF_MODEL, DEFAULT_MODEL),
            name=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )
        self._attr_default_language = entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        self._attr_default_options = self._build_default_options()
        self._apply_voices(entry.runtime_data.voices)

    @property
    def _client(self) -> CartesiaClient:
        return self._entry.runtime_data.client

    @callback
    def _apply_voices(self, voices: list[dict[str, Any]]) -> None:
        """Refresh the cached voice list and everything derived from it."""
        self._voices = voices
        languages = set(derive_languages(voices))
        if not languages:
            # /voices was unreachable; keep the entity usable with the stored
            # default voice instead of exposing no languages at all.
            languages = set(FALLBACK_LANGUAGES)
        # Home Assistant rejects a request whose language is not advertised, so
        # the configured default has to be part of the list either way.
        languages.add(self._attr_default_language)
        self._attr_supported_languages = sorted(languages)

        # Prefer the Cartesia code that this account's voices actually use over
        # the static reverse map (relevant for e.g. "nb" vs "no").
        present = {v.get("language") for v in voices if v.get("language")}
        self._ha_to_cartesia = dict(HA_TO_CARTESIA)
        for code in present:
            for ha_code in CARTESIA_TO_HA.get(code, [code]):
                self._ha_to_cartesia[ha_code] = code

    @override
    async def async_added_to_hass(self) -> None:
        """Start the periodic voice-list refresh."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_refresh_voices,
                timedelta(hours=VOICES_REFRESH_INTERVAL_HOURS),
            )
        )

    async def _async_refresh_voices(self, _now: Any = None) -> None:
        """Re-read /voices so new or removed voices show up without a reload."""
        try:
            voices = await self._client.list_voices()
        except CartesiaAuthError:
            self._entry.async_start_reauth(self.hass)
            return
        except CartesiaError as err:
            _LOGGER.debug("Voice refresh failed, keeping cached list: %s", err)
            return

        if voices and voices != self._voices:
            self._entry.runtime_data.voices = voices
            self._apply_voices(voices)
            self.async_write_ha_state()

    def _build_default_options(self) -> Mapping[str, Any]:
        """Return the defaults configured in the options flow.

        The entry is reloaded whenever options change, so a snapshot taken at
        construction time stays current.
        """
        options: dict[str, Any] = {
            ATTR_MODEL: self._entry.options.get(CONF_MODEL, DEFAULT_MODEL),
            CONF_SPEED: self._entry.options.get(CONF_SPEED),
            CONF_EMOTION: self._entry.options.get(CONF_EMOTION),
            CONF_VOLUME: self._entry.options.get(CONF_VOLUME),
            ATTR_VOICE: self._entry.options.get(CONF_VOICE),
        }
        return {key: value for key, value in options.items() if value is not None}

    @override
    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return the Cartesia voices available for a language."""
        code = self._to_cartesia_language(language)
        voices = [
            Voice(voice["id"], self._voice_label(voice))
            for voice in self._voices
            if voice.get("id") and voice.get("language") == code
        ]
        return sorted(voices, key=lambda voice: voice.name) or None

    @override
    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        """Synthesize a complete message and return MP3 bytes."""
        request = self._resolve(options, language)
        try:
            audio = await self._client.synthesize_bytes(
                model=request.model,
                transcript=message,
                voice_id=request.voice_id,
                language=request.language,
                generation_config=request.generation_config,
            )
        except CartesiaError as err:
            self._handle_error(err)
            if (fallback := await self._async_fallback(message, language)) is not None:
                return fallback
            raise HomeAssistantError(f"Cartesia TTS request failed: {err}") from err

        self._entry.runtime_data.usage.async_add_characters(len(message))
        self._clear_quota_issue()
        return "mp3", audio

    @override
    async def async_stream_tts_audio(
        self, request: TTSAudioRequest
    ) -> TTSAudioResponse:
        """Stream audio while the message is still arriving.

        The first chunk is awaited before the response is handed back, so a
        failure that happens before any audio exists can still be answered with
        the fallback engine. Emitting the WAV header first would commit Home
        Assistant to a stream that then simply stops - audible as nothing at
        all, with only a log line to show for it.
        """
        if not self._entry.options.get(CONF_STREAMING, True):
            return await super().async_stream_tts_audio(request)

        recorded = _RecordedMessage(request.message_gen, self._entry.runtime_data.usage)
        chunks = self._cartesia_pcm(request, recorded)
        try:
            first = await anext(chunks)
        except StopAsyncIteration:
            await chunks.aclose()
            return await self._async_fallback_response(
                CartesiaError("Cartesia returned no audio"), recorded, request.language
            )
        except CartesiaError as err:
            await chunks.aclose()
            return await self._async_fallback_response(err, recorded, request.language)

        self._clear_quota_issue()
        return TTSAudioResponse("wav", self._wav_stream(first, chunks))

    async def _cartesia_pcm(
        self, request: TTSAudioRequest, recorded: _RecordedMessage
    ) -> AsyncGenerator[bytes]:
        """Yield raw PCM from Cartesia, without a container header."""
        resolved = self._resolve(request.options, request.language)
        async for chunk in self._client.synthesize_stream(
            model=resolved.model,
            transcript_gen=recorded.stream(),
            voice_id=resolved.voice_id,
            language=resolved.language,
            generation_config=resolved.generation_config,
        ):
            yield chunk

    async def _wav_stream(
        self, first: bytes, chunks: AsyncGenerator[bytes]
    ) -> AsyncGenerator[bytes]:
        """Yield the WAV header, then the audio that is already flowing."""
        yield wav_header()
        yield first
        try:
            async for chunk in chunks:
                yield chunk
        except CartesiaError as err:
            # Playback has already started, so there is no switching engines
            # now; make sure it is at least loud in the log.
            self._handle_error(err)
            raise HomeAssistantError(f"Cartesia TTS stream failed: {err}") from err

    async def _async_fallback_response(
        self, err: CartesiaError, recorded: _RecordedMessage, language: str
    ) -> TTSAudioResponse:
        """Answer a failed stream from the fallback engine, or give up loudly."""
        self._handle_error(err)
        message = await recorded.drain()
        if (fallback := await self._async_fallback(message, language)) is not None:
            extension, data = fallback

            async def single_chunk() -> AsyncGenerator[bytes]:
                yield data

            return TTSAudioResponse(extension, single_chunk())
        raise HomeAssistantError(f"Cartesia TTS stream failed: {err}")

    async def _async_fallback(
        self, message: str, language: str
    ) -> tuple[str, bytes] | None:
        """Synthesize through the configured fallback TTS entity.

        Returns None when no fallback is configured or it cannot be used, so
        the caller raises the original Cartesia error instead.
        """
        engine_id = self._entry.options.get(CONF_FALLBACK_ENGINE)
        if not engine_id or engine_id == self.entity_id:
            return None
        component = self.hass.data.get(DATA_COMPONENT)
        engine = component.get_entity(engine_id) if component else None
        if engine is None:
            _LOGGER.error("Fallback TTS engine %s is not available", engine_id)
            return None

        # The fallback has its own voices and options; passing Cartesia's
        # through would mean nothing to it, so it runs on its own defaults.
        supported = engine.supported_languages or []
        fallback_language = (
            language if language in supported else engine.default_language
        )
        try:
            extension, data = await engine.async_internal_get_tts_audio(
                message, fallback_language, {}
            )
        except Exception as fallback_err:  # noqa: BLE001 - never mask the Cartesia error
            _LOGGER.error(
                "Fallback TTS engine %s failed as well: %s", engine_id, fallback_err
            )
            return None

        if not data or not extension:
            _LOGGER.error("Fallback TTS engine %s returned no audio", engine_id)
            return None

        _LOGGER.warning(
            "Cartesia failed, spoke through the fallback engine %s instead", engine_id
        )
        return extension, data

    @callback
    def _handle_error(self, err: CartesiaError) -> None:
        """Make a failure visible: reauth, a repair issue, or at least a log."""
        if isinstance(err, CartesiaQuotaError):
            _LOGGER.error("Cartesia is out of credits: %s", err)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_QUOTA_EXHAUSTED,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_QUOTA_EXHAUSTED,
                learn_more_url=USAGE_URL,
            )
            return
        if isinstance(err, CartesiaAuthError):
            _LOGGER.error("Cartesia rejected the API key: %s", err)
            self._entry.async_start_reauth(self.hass)
            return
        _LOGGER.error("Cartesia TTS request failed: %s", err)

    @callback
    def _clear_quota_issue(self) -> None:
        """Drop the out-of-credits repair once synthesis works again."""
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_QUOTA_EXHAUSTED)

    def _resolve(
        self, options: dict[str, Any] | None, language: str | None
    ) -> _Request:
        """Merge per-call options with the configured defaults."""
        options = options or {}
        model = options.get(ATTR_MODEL) or self._entry.options.get(
            CONF_MODEL, DEFAULT_MODEL
        )
        voice_id = options.get(ATTR_VOICE) or self._entry.options.get(CONF_VOICE)
        if not voice_id:
            raise HomeAssistantError(
                "No Cartesia voice configured. Set a default voice in the"
                " integration options or pass one via the voice option."
            )
        code = self._to_cartesia_language(language)
        self._log_language_mismatch(voice_id, code)
        return _Request(
            model=model,
            voice_id=voice_id,
            language=code,
            generation_config=build_generation_config(
                speed=options.get(CONF_SPEED, self._entry.options.get(CONF_SPEED)),
                emotion=options.get(
                    CONF_EMOTION, self._entry.options.get(CONF_EMOTION)
                ),
                volume=options.get(CONF_VOLUME, self._entry.options.get(CONF_VOLUME)),
                model=model,
            ),
        )

    def _log_language_mismatch(self, voice_id: str, language: str | None) -> None:
        """Note when a voice is used outside the language it was built for.

        This is legal and sometimes wanted, but it is also the most common
        cause of "the German sounds American" reports, so it belongs in the log.
        """
        if not language:
            return
        voice = next((v for v in self._voices if v.get("id") == voice_id), None)
        if voice is None or not (voice_language := voice.get("language")):
            return
        if voice_language != language:
            _LOGGER.debug(
                "Voice %s is a %s voice but the request is %s; expect the accent"
                " of the voice, not of the language",
                voice.get("name") or voice_id,
                voice_language,
                language,
            )

    def _to_cartesia_language(self, language: str | None) -> str | None:
        """Translate an HA locale into a Cartesia ISO 639-1 code."""
        if not language:
            return None
        if code := self._ha_to_cartesia.get(language):
            return code
        return language.split("-", 1)[0].lower()

    @staticmethod
    def _voice_label(voice: dict[str, Any]) -> str:
        """Build a picker label like 'Skylar (US) - Friendly Guide'."""
        name = voice.get("name") or voice["id"]
        if country := voice.get("country"):
            name = f"{name} ({country})"
        if tagline := voice.get("tagline"):
            name = f"{name} - {tagline}"
        return name
