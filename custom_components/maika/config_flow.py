"""Config flow for MAIKA."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    MaikaApiClient,
    MaikaApiError,
    MaikaAuthenticationError,
)
from .const import (
    CONF_CLIENT_ID,
    CONF_PASSWORD,
    CONF_PHONE_NUMBER,
    CONF_SESSION_ID,
    DOMAIN,
)
from .phone import normalize_login_identifier

_LOGGER = logging.getLogger(__name__)


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
    title = str(account.get("full_name") or account.get("calling_name") or "MAIKA")
    return {"title": title, "unique_id": str(account["id"])}


class MaikaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a MAIKA config flow."""

    VERSION = 4

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start MAIKA account setup."""
        return await self.async_step_account(user_input)

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
