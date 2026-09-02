"""Tests for the Cartesia TTS entity."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.tts import ATTR_VOICE
from homeassistant.components.tts.const import DATA_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cartesia_tts.api import CartesiaAuthError, CartesiaError
from custom_components.cartesia_tts.const import (
    CONF_EMOTION,
    CONF_SPEED,
    CONF_VOLUME,
)
from custom_components.cartesia_tts.tts import CartesiaTTSEntity, derive_languages

ENTITY_ID = "tts.cartesia_sonic_tts"


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up the config entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entity(hass: HomeAssistant) -> CartesiaTTSEntity:
    """Return the TTS entity object itself."""
    return next(iter(hass.data[DATA_COMPONENT].entities))


# ------------------------------------------------------------- languages ----
def test_derive_languages_maps_and_keeps_unknown_codes() -> None:
    languages = derive_languages(
        [{"language": "de"}, {"language": "en"}, {"language": "zz"}]
    )
    assert "de-DE" in languages
    assert "en-US" in languages and "en-GB" in languages
    # An unmapped Cartesia code must survive verbatim rather than vanish.
    assert "zz" in languages


def test_derive_languages_ignores_voices_without_a_language() -> None:
    assert derive_languages([{"id": "x"}, {"language": None}]) == []


# ---------------------------------------------------------------- entity ----
async def test_entity_is_created_with_dynamic_languages(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get(ENTITY_ID)
    assert state is not None

    tts_entity = entity(hass)
    assert "de-DE" in tts_entity.supported_languages
    assert "zz" in tts_entity.supported_languages
    assert tts_entity.default_language == "de-DE"


async def test_supported_voices_are_filtered_by_language(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)

    german = tts_entity.async_get_supported_voices("de-DE")
    assert {voice.voice_id for voice in german} == {"de-voice-1", "de-voice-2"}
    # Sorted by label, so Anton comes before Greta.
    assert [voice.voice_id for voice in german] == ["de-voice-2", "de-voice-1"]

    english = tts_entity.async_get_supported_voices("en-US")
    assert {voice.voice_id for voice in english} == {"en-voice-1"}


async def test_get_tts_audio_uses_configured_defaults(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)

    extension, audio = await tts_entity.async_get_tts_audio(
        "Hallo Welt.", "de-DE", tts_entity.default_options or {}
    )

    assert extension == "mp3"
    assert audio == b"ID3fake-mp3"
    call = mock_client.synthesize_bytes.await_args.kwargs
    assert call["model"] == "sonic-3.5"
    assert call["voice_id"] == "de-voice-1"
    assert call["language"] == "de"
    assert call["generation_config"] == {"speed": 1.0, "emotion": "calm", "volume": 1.0}


async def test_per_call_options_override_the_defaults(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)

    await tts_entity.async_get_tts_audio(
        "Psst.",
        "de-DE",
        {
            ATTR_VOICE: "de-voice-2",
            ATTR_MODEL: "sonic-3",
            CONF_SPEED: "slow",
            CONF_VOLUME: 0.5,
            CONF_EMOTION: "scared",
        },
    )

    call = mock_client.synthesize_bytes.await_args.kwargs
    assert call["voice_id"] == "de-voice-2"
    assert call["model"] == "sonic-3"
    assert call["generation_config"] == {
        "speed": 0.8,
        "volume": 0.5,
        "emotion": "scared",
    }


async def test_controls_are_dropped_on_a_legacy_model(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)

    await tts_entity.async_get_tts_audio(
        "Hallo.", "de-DE", {ATTR_MODEL: "sonic-turbo", CONF_SPEED: 1.4}
    )
    assert mock_client.synthesize_bytes.await_args.kwargs["generation_config"] is None


async def test_missing_voice_raises(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    config_entry_without_voice: MockConfigEntry,
) -> None:
    await setup_entry(hass, config_entry_without_voice)
    tts_entity = entity(hass)

    with pytest.raises(HomeAssistantError, match="No Cartesia voice"):
        await tts_entity.async_get_tts_audio("Hallo.", "de-DE", {})


async def test_api_error_becomes_home_assistant_error(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)
    mock_client.synthesize_bytes.side_effect = CartesiaError("upstream down")

    with pytest.raises(HomeAssistantError, match="upstream down"):
        await tts_entity.async_get_tts_audio("Hallo.", "de-DE", {})


async def test_auth_error_starts_reauth(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = entity(hass)
    mock_client.synthesize_bytes.side_effect = CartesiaAuthError("revoked")

    with pytest.raises(HomeAssistantError):
        await tts_entity.async_get_tts_audio("Hallo.", "de-DE", {})
    await hass.async_block_till_done()

    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_setup_survives_an_unreachable_voice_list(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A /voices outage must not block setup; the stored voice still works."""
    mock_client.list_voices.side_effect = CartesiaError("offline")
    await setup_entry(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    tts_entity = entity(hass)
    assert "de-DE" in tts_entity.supported_languages

    await tts_entity.async_get_tts_audio("Hallo.", "de-DE", {})
    assert mock_client.synthesize_bytes.await_args.kwargs["voice_id"] == "de-voice-1"
