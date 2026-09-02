"""Tests for the credit usage sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.tts.const import DATA_COMPONENT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cartesia_tts.usage import UsageTracker, current_period

USED = "sensor.cartesia_credits_used"
LEFT = "sensor.cartesia_credits_remaining"


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# ------------------------------------------------------------- pure tracker --
def test_tracker_counts_characters_and_computes_remaining() -> None:
    tracker = UsageTracker(20000)
    tracker.async_add_characters(120)
    tracker.async_add_characters(80)

    assert tracker.used == 200
    assert tracker.remaining == 19800
    assert tracker.source == "local"


def test_tracker_prefers_the_api_figure() -> None:
    tracker = UsageTracker(20000)
    tracker.async_add_characters(200)
    tracker.async_set_api_used(4321)

    assert tracker.used == 4321
    assert tracker.source == "api"
    assert tracker.remaining == 20000 - 4321


def test_tracker_without_an_allowance_has_no_remaining() -> None:
    tracker = UsageTracker(0)
    tracker.async_add_characters(10)
    assert tracker.remaining is None


def test_tracker_never_reports_negative_remaining() -> None:
    tracker = UsageTracker(100)
    tracker.async_add_characters(250)
    assert tracker.remaining == 0


def test_tracker_ignores_a_restore_from_another_month() -> None:
    tracker = UsageTracker(20000)
    tracker.async_restore(5000, "1999-01")
    assert tracker.used == 0

    tracker.async_restore(5000, current_period())
    assert tracker.used == 5000


def test_tracker_notifies_listeners() -> None:
    tracker = UsageTracker(20000)
    seen: list[int] = []
    remove = tracker.async_add_listener(lambda: seen.append(tracker.used))

    tracker.async_add_characters(5)
    remove()
    tracker.async_add_characters(5)

    assert seen == [5]


# ------------------------------------------------------------------ entities --
async def test_sensors_are_created(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)

    used = hass.states.get(USED)
    left = hass.states.get(LEFT)
    assert used is not None
    assert left is not None
    assert used.state == "0"
    assert left.state == "20000"
    assert used.attributes["source"] == "local"
    assert used.attributes["allowance"] == 20000


async def test_synthesis_counts_against_the_allowance(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    tts_entity = next(iter(hass.data[DATA_COMPONENT].entities))

    message = "The washing machine has finished."
    await tts_entity.async_get_tts_audio(message, "de-DE", {})
    await hass.async_block_till_done()

    assert hass.states.get(USED).state == str(len(message))
    assert hass.states.get(LEFT).state == str(20000 - len(message))


async def test_usage_api_is_not_polled_without_an_admin_key(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    mock_client.has_admin_key = False
    await setup_entry(hass, config_entry)
    mock_client.usage_credits.assert_not_awaited()


async def test_admin_key_switches_the_source_to_the_api(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """With an admin key the exact figures replace the local estimate."""
    mock_client.has_admin_key = True
    mock_client.usage_credits.return_value = 7321
    await setup_entry(hass, config_entry)

    used = hass.states.get(USED)
    assert used.state == "7321"
    assert used.attributes["source"] == "api"
    assert hass.states.get(LEFT).state == str(20000 - 7321)


async def test_unusable_api_figures_are_ignored(
    hass: HomeAssistant, mock_client: AsyncMock, config_entry: MockConfigEntry
) -> None:
    """A malformed usage response must not poison the counter."""
    mock_client.has_admin_key = True
    mock_client.usage_credits.return_value = None
    await setup_entry(hass, config_entry)

    used = hass.states.get(USED)
    assert used.state == "0"
    assert used.attributes["source"] == "local"
