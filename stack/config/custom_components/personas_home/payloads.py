"""Endpoint and payload construction for Personas Studio.

This module imports NOTHING from Home Assistant, on purpose. Every wire format
the integration produces is built here so that `make test` — python3 alone, no
homeassistant package — can assert them. The rest of the integration only
transports what these functions return.

The three payload shapes are NOT interchangeable, and the difference is easy to
miss: ``details`` carries a plain string on the event channel and an object on
the feedback channel. That is why the two live behind two services rather than
one service with a branch.
"""
from __future__ import annotations

from urllib.parse import urlsplit


def webhook_endpoint(url: str, persona: str) -> str:
    """The endpoint a channel posts to, from either shape of configured URL.

    TWO SHAPES ARE ACCEPTED, and which one is meant is read off the URL itself:

    * **no path** — ``http://127.0.0.1:8080`` — is a personas-studio server, and
      the persona names the webhook on it: ``.../webhook/Hestia``;
    * **a path** — ``https://iris…/h/<secret>`` — is already the whole endpoint
      and is used verbatim. That is the shape of Bleuenn's webhooks, where the
      secret IS the path; appending anything to one yields 404.

    Getting the shape wrong is not recoverable at run time: the services are
    fire-and-forget, so a 404 reaches nobody. The config flow probes the
    endpoint this function returns, which is where a mistake still surfaces.

    A URL with no scheme is treated as a base: urlsplit would otherwise read
    ``127.0.0.1`` as the scheme and the rest as a path, silently switching to
    verbatim mode and dropping the persona.
    """
    trimmed = url.rstrip("/")
    if is_complete_endpoint(trimmed):
        return trimmed
    return f"{trimmed}/webhook/{persona}"


def is_complete_endpoint(url: str) -> bool:
    """Whether this URL already names its correspondent.

    True for ``https://iris…/h/<secret>``, false for ``http://127.0.0.1:8080``.
    Callers use it to say out loud that a persona cannot apply, rather than
    dropping it in silence.
    """
    trimmed = url.rstrip("/")
    # No scheme: urlsplit would read `127.0.0.1` as the scheme and the rest as
    # a path, and a plain host:port would masquerade as a complete endpoint.
    if "://" not in trimmed:
        return False
    return bool(urlsplit(trimmed).path)


def auth_headers(token: str | None) -> dict:
    """The Authorization header for a channel, or none at all.

    The token is per channel and optional. Bleuenn's webhooks carry their secret
    in the path and need none; sending them the studio's bearer token would
    disclose it to a host that has no business with it.
    """
    cleaned = (token or "").strip()
    return {"Authorization": f"Bearer {cleaned}"} if cleaned else {}


def conversation_payload(
    text: str, conversation_id: str, language: str
) -> dict:
    """Synchronous request: the caller waits for the persona's reply."""
    return {
        "type": "conversation",
        "text": text,
        "conversation_id": conversation_id,
        "language": language,
    }


def event_payload(text: str, event_type: str) -> dict:
    """Fire-and-forget notification: ``details`` is the message itself."""
    return {
        "type": "event",
        "event_type": event_type,
        "details": text,
    }


def feedback_payload(
    entity_id: str, event_type: str, accepted: bool, suggestion_id: str
) -> dict:
    """Fire-and-forget feedback on a Home Assistant entity.

    Byte-for-byte the payload of the retired ``rest_command.ha_ai_feedback``.
    Its template lived in configuration.yaml, which no longer holds it; the
    contract now survives only in tests/test_personas_payloads.py.
    """
    return {
        "type": "event",
        "event_type": event_type,
        "entity_id": entity_id,
        "details": {"accepted": accepted, "suggestion_id": suggestion_id},
    }
