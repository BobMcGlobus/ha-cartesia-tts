"""Fixtures for the tests that boot a real Home Assistant."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cartesia_tts.const import (
    CONF_EMOTION,
    CONF_LANGUAGE,
    CONF_SPEED,
    CONF_STREAMING,
    CONF_VOICE,
    CONF_VOLUME,
    DOMAIN,
)

VOICES: list[dict[str, Any]] = [
    {
        "id": "de-voice-1",
        "name": "Greta",
        "language": "de",
        "country": "DE",
        "tagline": "Freundlich",
    },
    {"id": "de-voice-2", "name": "Anton", "language": "de", "country": "AT"},
    {"id": "en-voice-1", "name": "Skylar", "language": "en", "country": "US"},
    # A language the static map does not know, to prove it is not dropped.
    {"id": "xx-voice-1", "name": "Nova", "language": "zz"},
]

OPTIONS: dict[str, Any] = {
    "model": "sonic-3.5",
    CONF_LANGUAGE: "de-DE",
    CONF_VOICE: "de-voice-1",
    CONF_SPEED: 1.0,
    CONF_VOLUME: 1.0,
    CONF_EMOTION: "calm",
    CONF_STREAMING: False,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Let Home Assistant load the integration from custom_components/."""
    return


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch the Cartesia client everywhere the integration constructs one."""
    client = AsyncMock()
    client.list_voices.return_value = VOICES
    client.synthesize_bytes.return_value = b"ID3fake-mp3"

    with (
        patch("custom_components.cartesia_tts.CartesiaClient", return_value=client),
        patch(
            "custom_components.cartesia_tts.config_flow.CartesiaClient",
            return_value=client,
        ),
    ):
        yield client


def _entry(options: dict[str, Any]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Cartesia Sonic TTS",
        data={"api_key": "sk_car_test"},
        options=options,
    )


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured entry with a German default voice."""
    return _entry(dict(OPTIONS))


@pytest.fixture
def config_entry_without_voice() -> MockConfigEntry:
    """An entry that never got a default voice, e.g. an interrupted setup."""
    return _entry({k: v for k, v in OPTIONS.items() if k != CONF_VOICE})
