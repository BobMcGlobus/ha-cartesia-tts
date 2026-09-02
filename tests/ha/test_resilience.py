"""Tests for what happens when Cartesia will not answer.

Two ways to fail, both of which used to end in silence: no internet, and a
spent monthly allowance.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.tts import TTSAudioRequest
from homeassistant.components.tts.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cartesia_tts.api import (
    CartesiaConnectionError,
    CartesiaQuotaError,
)
from custom_components.cartesia_tts.const import DOMAIN, ISSUE_QUOTA_EXHAUSTED


class StubEngine:
    """A minimal stand-in for another TTS entity."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.supported_languages = ["en-US"]
        self.default_language = "en-US"

    async def async_internal_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> tuple[str, bytes]:
        self.calls.append((message, language, options))
        return "mp3", b"fallback-audio"


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entity(hass: HomeAssistant):
    return next(iter(hass.data[DATA_COMPONENT].entities))


def use_fallback(hass: HomeAssistant, engine: StubEngine):
    """Make the configured fallback entity id resolve to the stub."""
    return patch.object(hass.data[DATA_COMPONENT], "get_entity", return_value=engine)


async def text_stream(*chunks: str) -> AsyncGenerator[str]:
    for chunk in chunks:
        yield chunk


def issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_QUOTA_EXHAUSTED)


# ------------------------------------------------------------------ quota ----
async def test_quota_error_raises_a_repair_issue_not_a_reauth(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """An exhausted allowance is not a bad key and must not ask for a new one."""
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)
    mock_client.synthesize_bytes.side_effect = CartesiaQuotaError("out of credits")

    with pytest.raises(HomeAssistantError):
        await tts_entity.async_get_tts_audio("Hello.", "de-DE", {})
    await hass.async_block_till_done()

    reported = issue(hass)
    assert reported is not None
    assert reported.severity is ir.IssueSeverity.ERROR
    assert not [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == "reauth"
    ]


async def test_repair_issue_clears_once_synthesis_works_again(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)

    mock_client.synthesize_bytes.side_effect = CartesiaQuotaError("out of credits")
    with pytest.raises(HomeAssistantError):
        await tts_entity.async_get_tts_audio("Hello.", "de-DE", {})
    assert issue(hass) is not None

    mock_client.synthesize_bytes.side_effect = None
    await tts_entity.async_get_tts_audio("Hello again.", "de-DE", {})
    assert issue(hass) is None


# --------------------------------------------------------------- fallback ----
async def test_fallback_engine_answers_when_cartesia_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry_with_fallback: MockConfigEntry,
) -> None:
    await setup_entry(hass, config_entry_with_fallback)
    tts_entity = entity(hass)
    mock_client.synthesize_bytes.side_effect = CartesiaConnectionError("no internet")
    engine = StubEngine()

    with use_fallback(hass, engine):
        extension, audio = await tts_entity.async_get_tts_audio(
            "The washing machine has finished.", "de-DE", {}
        )

    assert (extension, audio) == ("mp3", b"fallback-audio")
    message, language, options = engine.calls[0]
    assert message == "The washing machine has finished."
    # de-DE is not in the stub's supported languages, so its default is used.
    assert language == "en-US"
    # Cartesia's options mean nothing to another engine.
    assert options == {}


async def test_without_a_fallback_the_error_still_surfaces(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)
    mock_client.synthesize_bytes.side_effect = CartesiaConnectionError("no internet")

    with pytest.raises(HomeAssistantError, match="no internet"):
        await tts_entity.async_get_tts_audio("Hello.", "de-DE", {})


async def test_a_broken_fallback_does_not_mask_the_cartesia_error(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry_with_fallback: MockConfigEntry,
) -> None:
    await setup_entry(hass, config_entry_with_fallback)
    tts_entity = entity(hass)
    mock_client.synthesize_bytes.side_effect = CartesiaConnectionError("no internet")

    engine = StubEngine()
    engine.async_internal_get_tts_audio = AsyncMock(
        side_effect=RuntimeError("also down")
    )

    with (
        use_fallback(hass, engine),
        pytest.raises(HomeAssistantError, match="no internet"),
    ):
        await tts_entity.async_get_tts_audio("Hello.", "de-DE", {})


# -------------------------------------------------------------- streaming ----
async def test_streaming_failure_before_any_audio_uses_the_fallback(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry_streaming: MockConfigEntry,
) -> None:
    """The regression this whole path exists for: no silent, truncated stream."""
    await setup_entry(hass, config_entry_streaming)
    tts_entity = entity(hass)

    async def failing_stream(**_kwargs: Any) -> AsyncGenerator[bytes]:
        raise CartesiaQuotaError("out of credits")
        yield b""  # pragma: no cover - unreachable, keeps this a generator

    mock_client.synthesize_stream = failing_stream
    engine = StubEngine()

    request = TTSAudioRequest(
        language="de-DE", options={}, message_gen=text_stream("Hello ", "world.")
    )
    with use_fallback(hass, engine):
        response = await tts_entity.async_stream_tts_audio(request)
        audio = b"".join([chunk async for chunk in response.data_gen])

    assert response.extension == "mp3"
    assert audio == b"fallback-audio"
    # The fallback needs the whole message, including the part not yet consumed.
    assert engine.calls[0][0] == "Hello world."
    assert issue(hass) is not None


async def test_streaming_emits_no_header_until_audio_actually_arrives(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry_streaming: MockConfigEntry,
) -> None:
    await setup_entry(hass, config_entry_streaming)
    tts_entity = entity(hass)

    async def good_stream(**_kwargs: Any) -> AsyncGenerator[bytes]:
        yield b"\x01\x02"
        yield b"\x03\x04"

    mock_client.synthesize_stream = good_stream
    request = TTSAudioRequest(
        language="de-DE", options={}, message_gen=text_stream("Hello.")
    )
    response = await tts_entity.async_stream_tts_audio(request)
    audio = b"".join([chunk async for chunk in response.data_gen])

    assert response.extension == "wav"
    assert audio.startswith(b"RIFF")
    assert audio.endswith(b"\x01\x02\x03\x04")
