"""Config flow for MAIKA."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    MaikaApiClient,
    MaikaApiError,
    MaikaAuthenticationError,
)
from .const import (
    CONF_CLIENT_ID,
    CONF_ENABLE_CLOUD_CAST,
    CONF_ENABLE_VOICE_COMMAND_SENSOR,
    CONF_PASSWORD,
    CONF_PHONE_NUMBER,
    CONF_SCAN_INTERVAL,
    CONF_SESSION_ID,
    CONF_VOICE_COMMAND_RULES,
    CONF_VOICE_SUCCESS_AUDIO_URL,
    CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VOICE_COMMAND_RULES,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .media_url import is_valid_http_media_url
from .phone import normalize_login_identifier
from .voice_rules import VoiceCommandRulesError, parse_voice_command_rules

_LOGGER = logging.getLogger(__name__)

def _maika_media_player_entity_ids(
    registry: er.EntityRegistry, entry_id: str
) -> tuple[str, ...]:
    return tuple(
        sorted(
            entry.entity_id
            for entry in er.async_entries_for_config_entry(registry, entry_id)
            if entry.domain == Platform.MEDIA_PLAYER
            and entry.platform == DOMAIN
            and entry.disabled_by is None
        )
    )


async def _async_validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, str]:
    client = MaikaApiClient(
        async_get_clientsession(hass),
        str(data[CONF_PHONE_NUMBER]),
        str(data[CONF_PASSWORD]),
        str(data.get(CONF_CLIENT_ID) or uuid4()),
        str(data.get(CONF_SESSION_ID) or uuid4()),
    )
    account = await client.async_login()
    await client.async_list_devices()
    title = str(account.get("full_name") or account.get("calling_name") or "MAIKA")
    return {"title": title, "unique_id": str(account["id"])}


class MaikaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a MAIKA config flow."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start MAIKA account setup directly."""
        return await self.async_step_account()

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create an entry from MAIKA account credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            normalized = {
                CONF_PHONE_NUMBER: normalize_login_identifier(
                    str(user_input[CONF_PHONE_NUMBER])
                ),
                CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
                CONF_CLIENT_ID: str(uuid4()),
                CONF_SESSION_ID: str(uuid4()),
            }
            try:
                info = await _async_validate_input(self.hass, normalized)
            except MaikaAuthenticationError:
                errors["base"] = "invalid_auth"
            except MaikaApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during MAIKA setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=normalized)

        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PHONE_NUMBER): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update expired or changed MAIKA credentials."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_failed")

        errors: dict[str, str] = {}
        if user_input is not None:
            updated_data = {
                **entry.data,
                CONF_PHONE_NUMBER: normalize_login_identifier(
                    str(user_input[CONF_PHONE_NUMBER])
                ),
                CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
            }
            try:
                info = await _async_validate_input(self.hass, updated_data)
            except MaikaAuthenticationError:
                errors["base"] = "invalid_auth"
            except MaikaApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during MAIKA reauthentication")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=updated_data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PHONE_NUMBER,
                        default=entry.data.get(CONF_PHONE_NUMBER, ""),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the MAIKA options flow."""
        return MaikaOptionsFlow()


class MaikaOptionsFlow(config_entries.OptionsFlow):
    """Configure polling and experimental cloud features."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the feature options directly."""
        return await self.async_step_features()

    async def async_step_features(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage integration polling and experimental feature options."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        values = dict(self.config_entry.options)
        registry = er.async_get(self.hass)
        media_player_entity_ids = _maika_media_player_entity_ids(
            registry, self.config_entry.entry_id
        )

        if user_input is not None:
            normalized = dict(user_input)
            selected_media_player = str(
                normalized.get(CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID, "")
            ).strip()
            if selected_media_player and (
                registry_entry := registry.async_get(selected_media_player)
            ):
                selected_media_player = registry_entry.entity_id
            if not selected_media_player and len(media_player_entity_ids) == 1:
                selected_media_player = media_player_entity_ids[0]
            normalized[CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID] = (
                selected_media_player
            )
            audio_url = str(normalized.get(CONF_VOICE_SUCCESS_AUDIO_URL, "")).strip()
            normalized[CONF_VOICE_SUCCESS_AUDIO_URL] = audio_url

            try:
                parse_voice_command_rules(
                    str(normalized.get(CONF_VOICE_COMMAND_RULES, ""))
                )
            except VoiceCommandRulesError as err:
                errors[CONF_VOICE_COMMAND_RULES] = "invalid_voice_command_rules"
                description_placeholders["rule_error"] = str(err)

            if audio_url:
                if not normalized.get(CONF_ENABLE_VOICE_COMMAND_SENSOR, False):
                    errors[CONF_VOICE_SUCCESS_AUDIO_URL] = (
                        "voice_success_audio_requires_voice_sensor"
                    )
                elif not normalized.get(CONF_ENABLE_CLOUD_CAST, False):
                    errors[CONF_VOICE_SUCCESS_AUDIO_URL] = (
                        "voice_success_audio_requires_cloud_cast"
                    )
                elif not is_valid_http_media_url(audio_url):
                    errors[CONF_VOICE_SUCCESS_AUDIO_URL] = (
                        "voice_success_audio_url_invalid"
                    )

                media_player_entity_id = normalized[
                    CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID
                ]
                if not media_player_entity_id:
                    errors[CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID] = (
                        "voice_success_audio_media_player_required"
                    )
                else:
                    media_player_registry_entry = registry.async_get(
                        media_player_entity_id
                    )
                    if (
                        media_player_registry_entry is None
                        or media_player_registry_entry.domain != Platform.MEDIA_PLAYER
                        or media_player_registry_entry.platform != DOMAIN
                        or media_player_registry_entry.config_entry_id
                        != self.config_entry.entry_id
                    ):
                        errors[CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID] = (
                            "voice_success_audio_media_player_invalid"
                        )

            if not errors:
                return self.async_create_entry(title="", data=normalized)
            values = normalized

        if len(media_player_entity_ids) == 1:
            values[CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID] = media_player_entity_ids[
                0
            ]
        media_player_entity_marker = vol.Optional(
            CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID
        )
        if values.get(CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID):
            media_player_entity_marker = vol.Optional(
                CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID,
                default=values[CONF_VOICE_SUCCESS_MEDIA_PLAYER_ENTITY_ID],
            )

        schema: dict[vol.Marker, Any] = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                ),
            ),
            vol.Optional(
                CONF_ENABLE_CLOUD_CAST,
                default=values.get(CONF_ENABLE_CLOUD_CAST, False),
            ): bool,
            vol.Optional(
                CONF_ENABLE_VOICE_COMMAND_SENSOR,
                default=values.get(CONF_ENABLE_VOICE_COMMAND_SENSOR, False),
            ): bool,
            vol.Optional(
                CONF_VOICE_COMMAND_RULES,
                default=values.get(
                    CONF_VOICE_COMMAND_RULES, DEFAULT_VOICE_COMMAND_RULES
                ),
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional(
                CONF_VOICE_SUCCESS_AUDIO_URL,
                default=values.get(CONF_VOICE_SUCCESS_AUDIO_URL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
        }
        if len(media_player_entity_ids) != 1:
            schema[media_player_entity_marker] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=Platform.MEDIA_PLAYER,
                    integration=DOMAIN,
                )
            )

        return self.async_show_form(
            step_id="features",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders=description_placeholders,
        )
