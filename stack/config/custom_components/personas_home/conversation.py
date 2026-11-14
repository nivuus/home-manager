"""Conversation entity for Personas Studio."""
from __future__ import annotations

import logging
import uuid
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, translation
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PERSONA, CONF_TOKEN, CONF_URL, DEFAULT_TIMEOUT, DOMAIN, NAME
from .payloads import (
    auth_headers,
    conversation_payload,
    endpoint_for_log,
    error_for_log,
    failure_key,
    webhook_endpoint,
)

_LOGGER = logging.getLogger(__name__)


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

        intent_response = intent.IntentResponse(language=user_input.language)
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
            if not isinstance(data, dict):
                raise ValueError(f"reply is a {type(data).__name__}, not an object")
            intent_response.async_set_speech(data.get("response", ""))
            conversation_id = data.get("conversation_id", conversation_id)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            # A failure is an ERROR-typed response, so callers (automations, a
            # voice pipeline) can tell it from an answer. The diagnostic goes
            # to the log; the person hears a translated sentence instead.
            _LOGGER.warning(
                "Personas Studio conversation failed at %s: %s",
                endpoint_for_log(self._url, self._persona),
                error_for_log(err),
            )
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                await self._failure_speech(user_input, err),
            )

        return conversation.ConversationResult(
            response=intent_response,
            conversation_id=conversation_id,
        )

    async def _failure_speech(
        self, user_input: conversation.ConversationInput, err: Exception
    ) -> str:
        """The failure sentence in the turn's language.

        Home Assistant's own translation loader picks the file: the turn's
        language, then the instance's when the turn names none, and English
        for any key a language file lacks. tests/test_personas_payloads.py
        guarantees every failure key exists in every shipped file.
        """
        key = failure_key(
            err,
            response_error=aiohttp.ClientResponseError,
            connection_error=aiohttp.ClientConnectionError,
        )
        language = user_input.language or self._hass.config.language
        translations = await translation.async_get_translations(
            self._hass, language, "exceptions", [DOMAIN]
        )
        return translations[f"component.{DOMAIN}.exceptions.{key}.message"]
