"""Credit usage sensors for the Cartesia Sonic TTS integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, override

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from . import CartesiaConfigEntry
from .api import CartesiaError
from .const import (
    ATTR_ALLOWANCE,
    ATTR_PERIOD,
    ATTR_SOURCE,
    CONF_MONTHLY_ALLOWANCE,
    DOMAIN,
    MANUFACTURER,
    UNIT_CREDITS,
    USAGE_REFRESH_INTERVAL_MINUTES,
)
from .usage import UsageTracker, current_period, period_start

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CartesiaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the credit sensors."""
    async_add_entities(
        [CartesiaCreditsUsedSensor(entry), CartesiaCreditsLeftSensor(entry)]
    )


class CartesiaUsageSensor(SensorEntity):
    """Shared plumbing for the two credit sensors."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UNIT_CREDITS
    _attr_suggested_display_precision = 0

    def __init__(self, entry: CartesiaConfigEntry, key: str) -> None:
        """Initialize the sensor from the config entry."""
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            name=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _tracker(self) -> UsageTracker:
        return self._entry.runtime_data.usage

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the tracker for as long as the entity is loaded."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._tracker.async_add_listener(self.async_write_ha_state)
        )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose where the figure came from and which month it covers."""
        return {
            ATTR_SOURCE: self._tracker.source,
            ATTR_PERIOD: self._tracker.period,
            ATTR_ALLOWANCE: self._tracker.allowance,
        }


class CartesiaCreditsUsedSensor(CartesiaUsageSensor, RestoreSensor):
    """Credits consumed in the current month."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:counter"

    def __init__(self, entry: CartesiaConfigEntry) -> None:
        """Initialize the used-credits sensor."""
        super().__init__(entry, "credits_used")

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the local tally and start polling the usage API."""
        await super().async_added_to_hass()

        if (last := await self.async_get_last_sensor_data()) is not None and (
            last.native_value is not None
        ):
            state = await self.async_get_last_state()
            period = (state.attributes.get(ATTR_PERIOD) if state else None) or ""
            with_int = int(float(last.native_value))
            self._tracker.async_restore(with_int, period)

        if self._entry.runtime_data.client.has_admin_key:
            await self._async_refresh_usage()
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_refresh_usage,
                    timedelta(minutes=USAGE_REFRESH_INTERVAL_MINUTES),
                )
            )

    async def _async_refresh_usage(self, _now: Any = None) -> None:
        """Read the real consumption for this month from the admin API."""
        try:
            used = await self._entry.runtime_data.client.usage_credits(
                period_start(current_period()), dt_util.now()
            )
        except CartesiaError as err:
            _LOGGER.debug("Could not read Cartesia usage, keeping local tally: %s", err)
            return
        self._tracker.async_set_api_used(used)

    @property
    @override
    def native_value(self) -> int:
        """Return the credits consumed this month."""
        return self._tracker.used

    @property
    @override
    def last_reset(self) -> Any:
        """Return when the current counting period began."""
        return period_start(self._tracker.period)


class CartesiaCreditsLeftSensor(CartesiaUsageSensor):
    """Credits left against the configured monthly allowance."""

    _attr_icon = "mdi:gauge"

    def __init__(self, entry: CartesiaConfigEntry) -> None:
        """Initialize the remaining-credits sensor."""
        super().__init__(entry, "credits_remaining")

    @property
    @override
    def available(self) -> bool:
        """Only meaningful once an allowance is configured."""
        return bool(self._entry.options.get(CONF_MONTHLY_ALLOWANCE))

    @property
    @override
    def native_value(self) -> int | None:
        """Return the credits left this month."""
        return self._tracker.remaining
