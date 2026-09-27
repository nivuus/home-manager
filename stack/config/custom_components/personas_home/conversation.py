"""Conversation entity for Personas Studio."""
from __future__ import annotations

import uuid
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PERSONA, CONF_TOKEN, CONF_URL, DEFAULT_TIMEOUT, DOMAIN, NAME
from .payloads import auth_headers, conversation_payload, webhook_endpoint


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Personas Studio conversation entity from a config entry."""
    async_add_entities([PersonasConversationEntity(hass, entry)])


class PersonasConversationEntity(conversation.ConversationEntity):
    """Conversation entity that forwards requests to a persona.

    THE ONLY SYNCHRONOUS CHANNEL. It posts ``type: conversation`` and waits for
    the reply, exposing it as the conversation speech. Four callers depend on
    that reply — scripts.yaml and three automations, one of which parses the
    returned JSON — which is why this channel has its own URL and did not
    follow the life events to Bleuenn, whose webhook answers 202 with no body.

    For notifications that must not block the automation, use the services in
    __init__.py instead: ``personas_home.send_event`` for life events and
    ``personas_home.send_feedback`` for feedback on an entity.
    """

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Settings come from the entry's data overlaid with the options flow's
        # edits; reading entry.data alone would keep serving a URL the user has
        # already changed in the UI.
        config = {**entry.data, **entry.options}
        self._hass = hass
        self._entry = entry
        self._url: str = config[CONF_URL]
        self._token: str = config[CONF_TOKEN]
        self._persona: str = config[CONF_PERSONA]
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}"
        self._attr_device_info = None

    @property
    def name(self) -> str:
        return f"{NAME} ({self._persona})"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process a conversation turn by forwarding to the persona."""
        endpoint = webhook_endpoint(self._url, self._persona)
        conversation_id = user_input.conversation_id or str(uuid.uuid4())
        payload = conversation_payload(
            user_input.text, conversation_id, user_input.language
        )

        response_text = ""
        try:
            session = async_get_clientsession(self._hass)
            async with session.post(
                endpoint,
                json=payload,
                headers=auth_headers(self._token),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                response_text = data.get("response", "")
                conversation_id = data.get("conversation_id", conversation_id)
        except aiohttp.ClientResponseError as err:
            response_text = f"Error {err.status}: {err.message}"
        except aiohttp.ClientError as err:
            response_text = f"Connection error: {err}"
        except Exception as err:  # noqa: BLE001
            response_text = f"Unexpected error: {err}"

        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(response_text)

        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=conversation_id,
        )
