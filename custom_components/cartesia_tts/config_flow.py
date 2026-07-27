"""Config flow for the Cartesia Sonic TTS integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY, CONF_MODEL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import CartesiaConfigEntry
from .api import CartesiaAuthError, CartesiaClient, CartesiaConnectionError
from .const import (
    CONF_EMOTION,
    CONF_LANGUAGE,
    CONF_SPEED,
    CONF_STREAMING,
    CONF_VOICE,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DOMAIN,
    EMOTIONS,
    EMOTIONS_PRIMARY,
    MODELS,
    NAME,
    SPEED_DEFAULT,
    SPEED_MAX,
    SPEED_MIN,
)
from .tts import derive_languages

_LOGGER = logging.getLogger(__name__)

API_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
MODEL_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=MODELS, mode=SelectSelectorMode.DROPDOWN)
)
SPEED_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=SPEED_MIN, max=SPEED_MAX, step=0.05, mode=NumberSelectorMode.SLIDER
    )
)
# Cartesia flags the six primary emotions as the most reliable ones, so they go
# to the top of the dropdown.
EMOTION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=emotion, label=emotion)
            for emotion in [
                *EMOTIONS_PRIMARY,
                *(e for e in EMOTIONS if e not in EMOTIONS_PRIMARY),
            ]
        ],
        mode=SelectSelectorMode.DROPDOWN,
        sort=False,
    )
)


def _voice_selector(
    voices: list[dict[str, Any]], language: str | None
) -> SelectSelector | TextSelector:
    """Build a voice picker for a language, or a free-text field if unknown."""
    options = [
        SelectOptionDict(value=voice["id"], label=_voice_label(voice))
        for voice in _voices_for_language(voices, language)
    ]
    if not options:
        return TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
    return SelectSelector(
        SelectSelectorConfig(
            options=sorted(options, key=lambda option: option["label"]),
            mode=SelectSelectorMode.DROPDOWN,
            custom_value=True,
        )
    )


def _voice_default(
    voices: list[dict[str, Any]], language: str | None, current: Any
) -> Any:
    """Keep the stored voice as the default only if it fits the language."""
    if not current:
        return vol.UNDEFINED
    if not voices:  # nothing to check against, keep what is configured
        return current
    if any(voice["id"] == current for voice in _voices_for_language(voices, language)):
        return current
    return vol.UNDEFINED


def _voices_for_language(
    voices: list[dict[str, Any]], language: str | None
) -> list[dict[str, Any]]:
    """Filter voices by the Cartesia code behind an HA locale."""
    if not language:
        return [voice for voice in voices if voice.get("id")]
    code = language.split("-", 1)[0].lower()
    return [
        voice for voice in voices if voice.get("id") and voice.get("language") == code
    ]


def _voice_label(voice: dict[str, Any]) -> str:
    """Build a human readable label for a voice."""
    label = voice.get("name") or voice["id"]
    if country := voice.get("country"):
        label = f"{label} ({country})"
    if tagline := voice.get("tagline"):
        label = f"{label} - {tagline}"
    return label


def _language_selector(voices: list[dict[str, Any]]) -> SelectSelector:
    """Build the language picker from the languages Cartesia reports."""
    languages = derive_languages(voices) or [DEFAULT_LANGUAGE]
    return SelectSelector(
        SelectSelectorConfig(
            options=languages, mode=SelectSelectorMode.DROPDOWN, custom_value=True
        )
    )


async def _async_load_voices(hass: Any, api_key: str) -> list[dict[str, Any]]:
    """Validate the key by loading the voice list."""
    client = CartesiaClient(async_get_clientsession(hass), api_key)
    return await client.list_voices()


class CartesiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup, reauth and reconfiguration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state."""
        self._api_key: str = ""
        self._voices: list[dict[str, Any]] = []
        self._options: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: CartesiaConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return CartesiaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API key and validate it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            errors = await self._async_validate(api_key)
            if not errors:
                self._api_key = api_key
                return await self.async_step_model()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the model and the default language."""
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_voice()

        defaults = self._current_options()
        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL, default=defaults.get(CONF_MODEL, DEFAULT_MODEL)
                    ): MODEL_SELECTOR,
                    vol.Required(
                        CONF_LANGUAGE,
                        default=defaults.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
                    ): _language_selector(self._voices),
                    vol.Required(
                        CONF_STREAMING, default=defaults.get(CONF_STREAMING, True)
                    ): bool,
                }
            ),
        )

    async def async_step_voice(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the default voice, speed and emotion, then finish."""
        if user_input is not None:
            self._options.update(user_input)
            if not user_input.get(CONF_EMOTION):
                self._options.pop(CONF_EMOTION, None)
            data = {CONF_API_KEY: self._api_key}
            if self.source == SOURCE_RECONFIGURE:
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    data=data,
                    options=self._options,
                )
            return self.async_create_entry(title=NAME, data=data, options=self._options)

        defaults = self._current_options()
        language = self._options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_VOICE,
                default=_voice_default(
                    self._voices, language, defaults.get(CONF_VOICE)
                ),
            ): _voice_selector(self._voices, language),
            vol.Required(
                CONF_SPEED, default=defaults.get(CONF_SPEED, SPEED_DEFAULT)
            ): SPEED_SELECTOR,
            vol.Optional(
                CONF_EMOTION,
                description={"suggested_value": defaults.get(CONF_EMOTION)},
            ): EMOTION_SELECTOR,
        }
        return self.async_show_form(
            step_id="voice",
            data_schema=vol.Schema(schema),
            description_placeholders={"language": language},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a rejected API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key and keep every other setting."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            errors = await self._async_validate(api_key)
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-run the full setup with the current values prefilled."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            errors = await self._async_validate(api_key)
            if not errors:
                self._api_key = api_key
                self._options = dict(entry.options)
                return await self.async_step_model()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_API_KEY, default=entry.data[CONF_API_KEY]
                    ): API_KEY_SELECTOR
                }
            ),
            errors=errors,
        )

    async def _async_validate(self, api_key: str) -> dict[str, str]:
        """Load the voice list and translate failures into form errors."""
        try:
            self._voices = await _async_load_voices(self.hass, api_key)
        except CartesiaAuthError:
            return {"base": "invalid_auth"}
        except CartesiaConnectionError:
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while validating the Cartesia API key")
            return {"base": "unknown"}
        return {}

    def _current_options(self) -> Mapping[str, Any]:
        """Return the values to prefill the forms with.

        Empty for a fresh setup, seeded from the entry when reconfiguring.
        """
        return self._options


class CartesiaOptionsFlow(OptionsFlowWithReload):
    """Change model, language, voice, speed and emotion after setup.

    Reloading is handled by the base class, so the integration deliberately
    registers no config entry update listener.
    """

    def __init__(self) -> None:
        """Initialize the options flow state."""
        self._options: dict[str, Any] = {}
        self._voices: list[dict[str, Any]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the model, language and streaming mode."""
        entry: CartesiaConfigEntry = self.config_entry
        if user_input is not None:
            self._options = {**entry.options, **user_input}
            return await self.async_step_voice()

        self._voices = await self._async_voices()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL,
                        default=entry.options.get(CONF_MODEL, DEFAULT_MODEL),
                    ): MODEL_SELECTOR,
                    vol.Required(
                        CONF_LANGUAGE,
                        default=entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
                    ): _language_selector(self._voices),
                    vol.Required(
                        CONF_STREAMING,
                        default=entry.options.get(CONF_STREAMING, True),
                    ): bool,
                }
            ),
        )

    async def async_step_voice(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the default voice, speed and emotion for the chosen language."""
        if user_input is not None:
            options = {**self._options, **user_input}
            if not user_input.get(CONF_EMOTION):
                options.pop(CONF_EMOTION, None)
            return self.async_create_entry(data=options)

        language = self._options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        return self.async_show_form(
            step_id="voice",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_VOICE,
                        default=_voice_default(
                            self._voices, language, self._options.get(CONF_VOICE)
                        ),
                    ): _voice_selector(self._voices, language),
                    vol.Required(
                        CONF_SPEED,
                        default=self._options.get(CONF_SPEED, SPEED_DEFAULT),
                    ): SPEED_SELECTOR,
                    vol.Optional(
                        CONF_EMOTION,
                        description={
                            "suggested_value": self._options.get(CONF_EMOTION)
                        },
                    ): EMOTION_SELECTOR,
                }
            ),
            description_placeholders={"language": language},
        )

    async def _async_voices(self) -> list[dict[str, Any]]:
        """Use the cached voice list, refetching it if setup came up empty."""
        entry: CartesiaConfigEntry = self.config_entry
        if entry.runtime_data.voices:
            return entry.runtime_data.voices
        try:
            voices = await entry.runtime_data.client.list_voices()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Could not load Cartesia voices for the options flow: %s", err
            )
            return []
        entry.runtime_data.voices = voices
        return voices
