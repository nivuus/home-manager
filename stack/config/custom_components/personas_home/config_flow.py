"""Config and options flow for Personas Studio.

The integration talks to THREE channels (see __init__.py), each with its own
URL, persona and token. All three are asked for here and all three stay
editable through the options flow — without it, changing a URL would mean
deleting the entry and losing the conversation entity's id along with the four
automations that name it.

Every channel is probed before an entry is written. The check is not cosmetic:
two of the three are fire-and-forget, so a wrong URL produces no error anywhere
at run time. This form is the only place a typo can still be caught — and it
probes the endpoint that will REALLY be used, base URL or complete one alike.
"""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CHANNELS,
    CONF_PERSONA,
    CONF_URL,
    CONFIG_VERSION,
    DEFAULT_PERSONA,
    DOMAIN,
    NAME,
)
from .payloads import auth_headers, webhook_endpoint

_LOGGER = logging.getLogger(__name__)

TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _probe(
    hass: HomeAssistant, url: str, persona: str, token: str
) -> str | None:
    """Probe one channel; return an error key or None.

    A ``type: event`` is used rather than a conversation: both the studio and
    Bleuenn answer it immediately instead of waking a model and making the form
    hang. The probe targets the endpoint webhook_endpoint() will really build —
    an earlier version always probed ``/webhook/home-manager``, so a mistyped
    persona validated green and failed silently forever after.

    A HOST THAT ANSWERS "NO" AND A HOST THAT DOES NOT ANSWER ARE NOT THE SAME
    MISTAKE, and this form must not conflate them:

    * the host replied 404/405/401 — it exists and rejects this endpoint. That
      is the typo this probe exists to catch, above all the base-URL/complete-
      URL confusion, which is silent everywhere else. REFUSED.
    * TLS failed — a wrong certificate is never "temporarily down"; it is a
      wrong host or something sitting in the middle. REFUSED. This branch is
      not hypothetical: a made-up ``*.ts.net`` name resolves through a wildcard
      and fails HERE, not on DNS, so ordering it after ClientConnectorError
      would file it under "simply down" and wave it through.
    * the name does not resolve — nothing of that name exists. REFUSED.
    * the host resolves, TLS is fine, but the connection is refused or times
      out — the correspondent is simply down. personas-studio was down the day
      the three channels were configured; refusing on that would make a
      legitimate configuration impossible to save, and the failure is loud at
      run time (a WARNING per call) rather than silent. ACCEPTED, with a log
      line.

    The three refusing branches are all subclasses of ClientConnectorError, so
    the order below is the behaviour, not a style preference.
    """
    endpoint = webhook_endpoint(url, persona)
    payload = {"type": "event", "event_type": "ha_integration_test"}
    try:
        session = async_get_clientsession(hass)
        async with session.post(
            endpoint,
            json=payload,
            headers=auth_headers(token),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (401, 403):
                return "invalid_auth"
            if resp.status not in (200, 202, 204):
                return "wrong_endpoint"
    except aiohttp.ClientSSLError:
        return "tls_failed"
    except aiohttp.ClientConnectorDNSError:
        return "unknown_host"
    except (aiohttp.ClientConnectorError, TimeoutError):
        _LOGGER.warning(
            "Personas Studio: %s does not answer; saved anyway, the "
            "correspondent may simply be down", endpoint
        )
        return None
    except aiohttp.ClientError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "unknown"
    return None


async def _validate(hass: HomeAssistant, values: dict) -> dict[str, str]:
    """Probe all three channels, reporting each failure on the field that owns it.

    They usually share a host; probing each one anyway is what makes a
    divergence visible on the right line of the form.
    """
    errors: dict[str, str] = {}
    for _name, url_key, persona_key, token_key in CHANNELS:
        error = await _probe(
            hass, values[url_key], values[persona_key], values[token_key]
        )
        if error:
            errors[url_key] = error
    return errors


def _normalise(user_input: dict, fallback: dict | None = None) -> dict:
    """Trim the form's answers and fill the blanks.

    An empty channel URL falls back to the conversation one, and an empty
    persona to the default: leaving a field empty is the honest way to say
    "same place as the main channel". Tokens do NOT inherit — a channel that
    was left without one must not silently receive another's secret.
    """
    source = {**(fallback or {}), **user_input}

    def text(key: str) -> str:
        return str(source.get(key, "") or "").strip()

    main_url = text(CONF_URL).rstrip("/")
    values: dict = {}
    for _name, url_key, persona_key, token_key in CHANNELS:
        values[url_key] = text(url_key).rstrip("/") or main_url
        values[persona_key] = text(persona_key) or DEFAULT_PERSONA
        values[token_key] = text(token_key)
    return values


def _schema(defaults: dict) -> vol.Schema:
    """The one form both flows show, pre-filled with what is already set."""
    fields: dict = {}
    for index, (_name, url_key, persona_key, token_key) in enumerate(CHANNELS):
        # The conversation URL is the one everything else falls back to, so it
        # is the only field that cannot be left empty.
        marker = vol.Required if index == 0 else vol.Optional
        fields[marker(url_key, default=defaults.get(url_key, ""))] = str
        fields[
            vol.Optional(
                persona_key, default=defaults.get(persona_key, DEFAULT_PERSONA)
            )
        ] = str
        fields[
            vol.Optional(token_key, default=defaults.get(token_key, ""))
        ] = TOKEN_SELECTOR
    return vol.Schema(fields)


class PersonasHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Personas Studio."""

    VERSION = CONFIG_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PersonasHomeOptionsFlow()

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        values: dict = {}

        if user_input is not None:
            values = _normalise(user_input)

            # Deduplicate on the conversation channel: one server can serve
            # several personas, and each deserves its own conversation entity.
            await self.async_set_unique_id(
                f"{values[CONF_URL]}#{values[CONF_PERSONA]}"
            )
            self._abort_if_unique_id_configured()

            errors = await _validate(self.hass, values)
            if not errors:
                return self.async_create_entry(title=NAME, data=values)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(values or {}),
            errors=errors,
        )


class PersonasHomeOptionsFlow(OptionsFlow):
    """Re-edit an existing entry's three channels.

    Answers are written to ``options``, which __init__.entry_config() layers on
    top of ``data``. The entry's unique_id keeps naming the URL and persona it
    was CREATED with — Home Assistant does not let an options flow change it.
    That id only rejects a duplicate at creation time, so a stale one is
    harmless; it is not a second source of truth for the settings.
    """

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            values = _normalise(user_input, fallback=current)
            errors = await _validate(self.hass, values)
            if not errors:
                return self.async_create_entry(data=values)
            current = values

        return self.async_show_form(
            step_id="init", data_schema=_schema(current), errors=errors
        )
