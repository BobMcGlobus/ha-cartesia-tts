"""The Cartesia Sonic TTS integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CartesiaAuthError, CartesiaClient, CartesiaError
from .const import CONF_ADMIN_KEY, CONF_MONTHLY_ALLOWANCE
from .usage import UsageTracker

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TTS]


@dataclass(kw_only=True, slots=True)
class CartesiaData:
    """Runtime data shared by the config entry's platforms."""

    client: CartesiaClient
    usage: UsageTracker
    voices: list[dict[str, Any]] = field(default_factory=list)


type CartesiaConfigEntry = ConfigEntry[CartesiaData]


async def async_setup_entry(hass: HomeAssistant, entry: CartesiaConfigEntry) -> bool:
    """Set up Cartesia Sonic TTS from a config entry."""
    client = CartesiaClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        admin_key=entry.options.get(CONF_ADMIN_KEY),
    )

    try:
        voices = await client.list_voices()
    except CartesiaAuthError as err:
        raise ConfigEntryAuthFailed("Cartesia rejected the API key") from err
    except CartesiaError as err:
        # The voice list only drives the picker and the language list. The
        # configured default voice is stored locally, so keep going and let the
        # entity fall back until the periodic refresh succeeds.
        _LOGGER.warning(
            "Could not load Cartesia voices during setup, continuing with defaults: %s",
            err,
        )
        voices = []

    entry.runtime_data = CartesiaData(
        client=client,
        usage=UsageTracker(entry.options.get(CONF_MONTHLY_ALLOWANCE)),
        voices=voices,
    )
    # No update listener on purpose: the options flow reloads the entry itself
    # (OptionsFlowWithReload), and a listener would clash with the reauth and
    # reconfigure flows calling async_update_reload_and_abort.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CartesiaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
