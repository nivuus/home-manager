"""Config and options flows for the Nivuus guest VPN.

The config flow stores how to reach the agent (host, port, token) and proves it
with a `vpn.get_status` call. The options flow is the upload form: a `.ovpn`,
an optional CA, the provider's username and password. It forwards them to the
agent and saves an EMPTY options dict -- the password and the profile never
land in the config entry, hence never in `.storage`, backups or diagnostics.

Options flows are admin-only in Home Assistant, which is the access control
this form relies on.
"""
from __future__ import annotations

import logging
from typing import Any

import probatio

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import AgentTimeout, CannotConnect, async_send_command
from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TOKEN,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
    ERROR_ACTIVATION,
    ERROR_AGENT,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_NOT_TEXT,
    ERROR_TIMEOUT,
    ERROR_UPLOAD_MISSING,
    FIELD_CA,
    FIELD_OVPN,
    FIELD_PASSWORD,
    FIELD_USERNAME,
    IMPORT_TIMEOUT,
    NAME,
    STATUS_TIMEOUT,
)
from .payloads import (
    AgentError,
    AuthRejected,
    UploadNotText,
    VpnStatus,
    build_import_command,
    build_status_command,
    parse_status,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST, default=DEFAULT_HOST): str,
        probatio.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
        probatio.Required(CONF_TOKEN): PASSWORD_SELECTOR,
    }
)

UPLOAD_SCHEMA = probatio.Schema(
    {
        probatio.Required(FIELD_OVPN): FileSelector(FileSelectorConfig(accept=".ovpn")),
        probatio.Optional(FIELD_CA): FileSelector(FileSelectorConfig(accept=".crt,.pem")),
        probatio.Required(FIELD_USERNAME): TextSelector(),
        probatio.Required(FIELD_PASSWORD): PASSWORD_SELECTOR,
    }
)


async def _call(
    hass: HomeAssistant, conn: dict[str, Any], command: dict, timeout: float
) -> tuple[VpnStatus | None, dict[str, str], str]:
    """Run one command; return (status, form errors, reason for the form)."""
    try:
        data = await async_send_command(
            hass, conn[CONF_HOST], conn[CONF_PORT], conn[CONF_TOKEN], command, timeout
        )
        return parse_status(data), {}, ""
    except AuthRejected:
        return None, {"base": ERROR_INVALID_AUTH}, ""
    except CannotConnect as err:
        _LOGGER.warning("Cannot reach the agent: %s", err)
        return None, {"base": ERROR_CANNOT_CONNECT}, ""
    except AgentTimeout as err:
        _LOGGER.warning("The agent did not answer: %s", err)
        return None, {"base": ERROR_TIMEOUT}, ""
    except AgentError as err:
        # The agent's reasons name fields, never their values.
        _LOGGER.warning("The agent refused the request: %s", err.reason)
        return None, {"base": ERROR_AGENT}, err.reason


class NivuusVpnConfigFlow(ConfigFlow, domain=DOMAIN):
    """Point Home Assistant at the agent's WebSocket API."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NivuusVpnOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reason = ""
        if user_input is not None:
            conn = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            self._async_abort_entries_match(
                {CONF_HOST: conn[CONF_HOST], CONF_PORT: conn[CONF_PORT]}
            )
            _status, errors, reason = await _call(
                self.hass, conn, build_status_command(), STATUS_TIMEOUT
            )
            if not errors:
                return self.async_create_entry(title=NAME, data=conn)
        # The token is never suggested back: a wrong one is typed again.
        suggested = {k: v for k, v in (user_input or {}).items() if k != CONF_TOKEN}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, suggested),
            errors=errors,
            description_placeholders={"reason": reason},
        )


def _read_upload(hass: HomeAssistant, file_id: str) -> bytes:
    """Read and delete one upload. Runs in the executor, as file_upload requires."""
    with process_uploaded_file(hass, file_id) as path:
        return path.read_bytes()


class NivuusVpnOptionsFlow(OptionsFlow):
    """Upload the guest VPN profile and credentials to the agent."""

    def __init__(self) -> None:
        # file_upload deletes a file once read. When the agent refuses the
        # import (a mistyped password, say), the form comes back with the same
        # file ids, which no longer exist; keeping the bytes until the flow ends
        # spares the owner picking the files again. Memory only, dropped with
        # the flow.
        self._uploads: dict[str, bytes] = {}

    async def _upload(self, file_id: str) -> bytes:
        if file_id not in self._uploads:
            self._uploads[file_id] = await self.hass.async_add_executor_job(
                _read_upload, self.hass, file_id
            )
        return self._uploads[file_id]

    async def _read_files(
        self, user_input: dict[str, Any]
    ) -> tuple[bytes | None, bytes | None, dict[str, str]]:
        files: dict[str, bytes | None] = {FIELD_OVPN: None, FIELD_CA: None}
        for field in files:
            if field not in user_input:
                continue
            try:
                files[field] = await self._upload(user_input[field])
            except ValueError:
                # process_uploaded_file: "File does not exist".
                return None, None, {field: ERROR_UPLOAD_MISSING}
        return files[FIELD_OVPN], files[FIELD_CA], {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reason = ""
        if user_input is not None:
            errors, reason = await self._import(user_input)
            if not errors:
                self._uploads.clear()
                return self.async_create_entry(data={})
        # Suggest the files and the username back, never the password.
        suggested = {k: v for k, v in (user_input or {}).items() if k != FIELD_PASSWORD}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(UPLOAD_SCHEMA, suggested),
            errors=errors,
            description_placeholders={"reason": reason},
        )

    async def _import(self, user_input: dict[str, Any]) -> tuple[dict[str, str], str]:
        """Send the upload to the agent; return (form errors, reason)."""
        ovpn, ca, errors = await self._read_files(user_input)
        if errors:
            return errors, ""
        try:
            command = build_import_command(
                ovpn, ca, user_input[FIELD_USERNAME], user_input[FIELD_PASSWORD]
            )
        except UploadNotText as err:
            field = FIELD_OVPN if err.field == "ovpn" else FIELD_CA
            return {field: ERROR_NOT_TEXT}, ""
        status, errors, reason = await _call(
            self.hass, self.config_entry.data, command, IMPORT_TIMEOUT
        )
        if status is not None and status.activation_failed:
            # The profile is stored on the agent, but the tunnel did not come
            # up. Saying "done" here would be the silent failure the spec rules
            # out: the owner sees the agent's reason instead.
            return {"base": ERROR_ACTIVATION}, status.last_error or ""
        return errors, reason
