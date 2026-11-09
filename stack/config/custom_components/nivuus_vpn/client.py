"""One command to the mqtt system agent, over its WebSocket API.

Each call opens a connection, authenticates, sends one command and waits for
its reply. The flows call the agent a handful of times in a lifetime; a
persistent connection would add reconnection logic for nothing.

The transport is Home Assistant's shared aiohttp session: no extra requirement,
and aiohttp sends no Origin header, which the agent requires of non-browser
clients. The wire format itself lives in payloads.py, under test.

Never log a command: its params carry the VPN password and the profile.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .payloads import (
    AuthRejected,
    ProtocolError,
    auth_message,
    check_auth_result,
    close_error,
    is_auth_required,
    reply_for,
)

_CLOSED = (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED)


class CannotConnect(Exception):
    """The agent could not be reached at all."""


class AgentTimeout(Exception):
    """The agent did not answer in time; the command may still be running."""


async def _receive(ws: aiohttp.ClientWebSocketResponse) -> Any:
    """The next JSON message, or the exception the agent's close means."""
    msg = await ws.receive()
    if msg.type == aiohttp.WSMsgType.TEXT:
        try:
            return json.loads(msg.data)
        except json.JSONDecodeError as err:
            raise ProtocolError("the agent sent a message that is not JSON") from err
    if msg.type in _CLOSED:
        code = ws.close_code if ws.close_code is not None else msg.data
        raise close_error(code if isinstance(code, int) else None, str(msg.extra or ""))
    if msg.type == aiohttp.WSMsgType.ERROR:
        raise CannotConnect(f"WebSocket error: {ws.exception()}")
    raise ProtocolError(f"unexpected WebSocket message type {msg.type.name}")


async def _authenticate(ws: aiohttp.ClientWebSocketResponse, token: str) -> None:
    first = await _receive(ws)
    if not is_auth_required(first):
        # The agent always asks; an open session would mean another server
        # is listening on this port.
        raise ProtocolError("the agent did not ask for authentication")
    await ws.send_json(auth_message(token))
    check_auth_result(await _receive(ws))


async def async_send_command(
    hass: HomeAssistant,
    host: str,
    port: int,
    token: str,
    command: dict[str, Any],
    timeout: float,
) -> Any:
    """Send ``command`` and return its reply's data.

    Raises CannotConnect, AuthRejected, AgentTimeout, or AgentError (with the
    agent's reason; ProtocolError included).
    """
    session = async_get_clientsession(hass)
    url = URL.build(scheme="ws", host=host, port=port)
    try:
        async with asyncio.timeout(timeout):
            async with session.ws_connect(url) as ws:
                await _authenticate(ws, token)
                await ws.send_json(command)
                while True:
                    done, data = reply_for(await _receive(ws), command["id"])
                    if done:
                        return data
    except TimeoutError as err:
        raise AgentTimeout(f"no reply within {timeout:.0f} s") from err
    except (aiohttp.ClientError, OSError) as err:
        raise CannotConnect(str(err) or type(err).__name__) from err


__all__ = ["AgentTimeout", "AuthRejected", "CannotConnect", "async_send_command"]
