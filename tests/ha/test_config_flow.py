"""Tests for the Cartesia config, reauth, reconfigure and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cartesia_tts.api import (
    CartesiaAuthError,
    CartesiaConnectionError,
)
from custom_components.cartesia_tts.const import (
    CONF_EMOTION,
    CONF_LANGUAGE,
    CONF_SPEED,
    CONF_STREAMING,
    CONF_VOICE,
    CONF_VOLUME,
    DOMAIN,
)


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """The happy path walks key -> model -> voice and stores the options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "sk_car_test"}
    )
    assert result["step_id"] == "model"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"model": "sonic-3.5", CONF_LANGUAGE: "de-DE", CONF_STREAMING: True},
    )
    assert result["step_id"] == "voice"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_VOICE: "de-voice-1",
            CONF_SPEED: 1.2,
            CONF_VOLUME: 0.8,
            CONF_EMOTION: "calm",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"api_key": "sk_car_test"}
    assert result["options"] == {
        "model": "sonic-3.5",
        CONF_LANGUAGE: "de-DE",
        CONF_STREAMING: True,
        CONF_VOICE: "de-voice-1",
        CONF_SPEED: 1.2,
        CONF_VOLUME: 0.8,
        CONF_EMOTION: "calm",
    }


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (CartesiaAuthError("nope"), "invalid_auth"),
        (CartesiaConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    side_effect: Exception,
    expected: str,
) -> None:
    """Every failure mode maps onto its own form error and stays recoverable."""
    mock_client.list_voices.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "bad"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    # A corrected key still gets through on the same flow.
    mock_client.list_voices.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "sk_car_test"}
    )
    assert result["step_id"] == "model"


async def test_single_entry_only(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """manifest.json declares single_config_entry."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth_replaces_the_key_and_keeps_options(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.list_voices.side_effect = CartesiaAuthError("still bad")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "still-bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.list_voices.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "sk_car_new"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["api_key"] == "sk_car_new"
    assert config_entry.options[CONF_VOICE] == "de-voice-1"


async def test_options_flow_roundtrip(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """Changing the language then the voice writes both back."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"model": "sonic-3", CONF_LANGUAGE: "en-US", CONF_STREAMING: True},
    )
    assert result["step_id"] == "voice"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_VOICE: "en-voice-1", CONF_SPEED: 0.9, CONF_VOLUME: 1.5},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options["model"] == "sonic-3"
    assert config_entry.options[CONF_LANGUAGE] == "en-US"
    assert config_entry.options[CONF_VOICE] == "en-voice-1"
    assert config_entry.options[CONF_VOLUME] == 1.5
    # Emotion was left empty on the form, so it must be cleared, not kept.
    assert CONF_EMOTION not in config_entry.options
