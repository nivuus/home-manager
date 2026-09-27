"""Personas Studio — Home Assistant integration.

THREE CHANNELS, EACH WITH ITS OWN URL, PERSONA AND TOKEN:

* **conversation** (``url``) — the conversation entity, see conversation.py.
  Synchronous: it posts ``type: conversation`` and waits for the persona's
  reply. Four callers depend on that reply, one of them parsing its JSON.
* **event** (``event_url``) — ``personas_home.send_event``, free-text life
  events, fire-and-forget.
* **feedback** (``feedback_url``) — ``personas_home.send_feedback``, structured
  feedback on a Home Assistant entity, fire-and-forget.

The feedback channel used to be a ``rest_command`` in configuration.yaml: a
hard-coded URL, and a copy of the bearer token in clear text. Bringing it here
is what let that block — and that second copy of the token — be deleted.

WHY THE TOKEN IS PER CHANNEL. The three correspondents are no longer the same
host. Bleuenn's webhooks carry their secret in the path and need no token at
all; sending them the studio's would disclose it to a host that has no business
with it. An empty token means no Authorization header, not an empty one.

BOTH SERVICES ARE FIRE-AND-FORGET, deliberately: they are called from
automations that must not stall behind a webhook. That also means a wrong URL
produces NO failure the caller can see — which is why every wire format they
build lives in payloads.py under test rather than inline here, and why a
refused or unreachable endpoint is at least written to the log.
"""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_EVENT_PERSONA,
    CONF_EVENT_TOKEN,
    CONF_EVENT_URL,
    CONF_FEEDBACK_PERSONA,
    CONF_FEEDBACK_TOKEN,
    CONF_FEEDBACK_URL,
    CONF_PERSONA,
    CONF_TOKEN,
    CONF_URL,
    CONFIG_VERSION,
    DEFAULT_PERSONA,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .payloads import (
    auth_headers,
    event_payload,
    feedback_payload,
    is_complete_endpoint,
    webhook_endpoint,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["conversation"]

SERVICE_SEND_EVENT = "send_event"
SERVICE_SEND_FEEDBACK = "send_feedback"

ATTR_TEXT = "text"
ATTR_PERSONA = "persona"
ATTR_EVENT_TYPE = "event_type"
ATTR_ENTITY_ID = "entity_id"
ATTR_ACCEPTED = "accepted"
ATTR_SUGGESTION_ID = "suggestion_id"

SEND_EVENT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_PERSONA): cv.string,
        vol.Optional(ATTR_EVENT_TYPE, default="ha_automation"): cv.string,
    }
)

SEND_FEEDBACK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.string,
        vol.Required(ATTR_EVENT_TYPE): cv.string,
        vol.Optional(ATTR_ACCEPTED, default=True): cv.boolean,
        vol.Optional(ATTR_SUGGESTION_ID, default="latest"): cv.string,
        vol.Optional(ATTR_PERSONA): cv.string,
    }
)


def entry_config(entry: ConfigEntry) -> dict:
    """The entry's effective settings: what the options flow edited wins.

    Everything is created in ``data`` by the config flow and re-edited into
    ``options`` afterwards, so reading either one alone gives a stale answer for
    half the fields.
    """
    return {**entry.data, **entry.options}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Personas Studio from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry_config(entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)

    # Without this, editing a URL in the options flow changes the stored entry
    # and nothing else: the running services keep the settings they were set up
    # with, and the UI shows a value that is not the one being used.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an entry created before the split up to date.

    Before version 3 the event channel WAS the conversation channel and one
    token served everything, so inheriting from them is what keeps an existing
    installation behaving exactly as it did. Whoever wants the events elsewhere
    then says so once, in the options flow.
    """
    if entry.version >= CONFIG_VERSION:
        return True

    data = {**entry.data}
    data.setdefault(CONF_EVENT_URL, data.get(CONF_URL, ""))
    data.setdefault(CONF_EVENT_PERSONA, data.get(CONF_PERSONA, DEFAULT_PERSONA))
    data.setdefault(CONF_EVENT_TOKEN, data.get(CONF_TOKEN, ""))
    data.setdefault(CONF_FEEDBACK_URL, data.get(CONF_URL, ""))
    data.setdefault(CONF_FEEDBACK_PERSONA, DEFAULT_PERSONA)
    data.setdefault(CONF_FEEDBACK_TOKEN, data.get(CONF_TOKEN, ""))
    hass.config_entries.async_update_entry(
        entry, data=data, version=CONFIG_VERSION
    )
    return True


async def _async_post(
    hass: HomeAssistant, url: str, persona: str, token: str, payload: dict
) -> None:
    """POST to a channel's endpoint without ever failing the caller.

    Fire-and-forget is the contract of both services — an automation must not
    stall behind a webhook — but silence is not the same as invisibility: what
    the caller does not see, the log does.
    """
    endpoint = webhook_endpoint(url, persona)
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            endpoint,
            json=payload,
            headers=auth_headers(token),
            timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
        ) as resp:
            await resp.read()
            if resp.status >= 400:
                _LOGGER.warning(
                    "Personas Studio refused %s: HTTP %s", endpoint, resp.status
                )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Personas Studio unreachable at %s: %s", endpoint, err)


def _resolve_persona(url: str, configured: str, override: str | None) -> str:
    """The persona to address, warning when the override cannot apply.

    A persona only names anything when the URL is a studio BASE. On a complete
    endpoint — Bleuenn's webhooks, where the secret is the path — the persona is
    already baked into the URL, so a `persona:` passed by an automation changes
    nothing. Dropping it in silence is how an automation ends up believing for
    months that it addresses someone it does not.
    """
    if override and override != configured and is_complete_endpoint(url):
        _LOGGER.warning(
            "Personas Studio: persona %r ignored, %s is a complete endpoint "
            "and already names its correspondent",
            override,
            url,
        )
        return configured
    return override or configured


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the two fire-and-forget services (once)."""

    def _first_config() -> dict | None:
        entries = list(hass.data.get(DOMAIN, {}).values())
        return entries[0] if entries else None

    async def _handle_send_event(call: ServiceCall) -> None:
        """POST a free-text life event on the EVENT channel."""
        config = _first_config()
        if config is None:
            return
        url = config[CONF_EVENT_URL]
        await _async_post(
            hass,
            url,
            _resolve_persona(
                url, config[CONF_EVENT_PERSONA], call.data.get(ATTR_PERSONA)
            ),
            config[CONF_EVENT_TOKEN],
            event_payload(call.data[ATTR_TEXT], call.data[ATTR_EVENT_TYPE]),
        )

    async def _handle_send_feedback(call: ServiceCall) -> None:
        """POST entity feedback on the FEEDBACK channel."""
        config = _first_config()
        if config is None:
            return
        url = config[CONF_FEEDBACK_URL]
        await _async_post(
            hass,
            url,
            _resolve_persona(
                url, config[CONF_FEEDBACK_PERSONA], call.data.get(ATTR_PERSONA)
            ),
            config[CONF_FEEDBACK_TOKEN],
            feedback_payload(
                entity_id=call.data[ATTR_ENTITY_ID],
                event_type=call.data[ATTR_EVENT_TYPE],
                accepted=call.data[ATTR_ACCEPTED],
                suggestion_id=call.data[ATTR_SUGGESTION_ID],
            ),
        )

    handlers = (
        (SERVICE_SEND_EVENT, _handle_send_event, SEND_EVENT_SCHEMA),
        (SERVICE_SEND_FEEDBACK, _handle_send_feedback, SEND_FEEDBACK_SCHEMA),
    )
    for name, handler, schema in handlers:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(DOMAIN, name, handler, schema=schema)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for name in (SERVICE_SEND_EVENT, SERVICE_SEND_FEEDBACK):
                hass.services.async_remove(DOMAIN, name)
    return unload_ok
