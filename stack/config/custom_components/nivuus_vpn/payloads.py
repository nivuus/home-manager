"""Wire format of the agent's WebSocket API, as nivuus_vpn uses it.

The agent is the mqtt system agent (nivuus/mqtt): src/websocket/sessionProtocol.ts
for the handshake and replies, src/features/vpn/vpnWebSocketHandlers.ts for the
`vpn` feature. Its session goes:

    server: {"type": "auth_required", ...}
    client: {"type": "auth", "token": ...}
    server: {"type": "auth_result", "success": bool}   (wrong token: closed, 1008)
    server: {"id": "welcome", "success": true, ...}
    client: {"id": <uuid>, "type": "action"|"query", "feature": "vpn",
             "action": ..., "params": {...}}
    server: {"id": <uuid>, "success": bool, "data"|"error": ...}

This module imports neither Home Assistant nor aiohttp: it is what `make test`
checks with python3 alone. The params carry the VPN password and the private
material of the profile, so nothing here logs, and no error raised here quotes
a value -- only field names and the agent's own reasons.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

FEATURE = "vpn"

# GuestVpnState in src/features/vpn/guestVpnStatus.ts.
STATES = ("connected", "connecting", "disabled", "no_config", "error")

# RFC 6455 policy violation: the agent's answer to a wrong or missing token.
CLOSE_POLICY_VIOLATION = 1008


class AgentError(Exception):
    """The agent refused or failed the command; ``reason`` is its own words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ProtocolError(AgentError):
    """The agent answered something this client does not understand."""


class AuthRejected(Exception):
    """The agent refused the token."""


class UploadNotText(ValueError):
    """An uploaded file is not UTF-8 text; ``field`` names which one."""

    def __init__(self, field: str) -> None:
        super().__init__(f"The {field} file is not UTF-8 text")
        self.field = field


@dataclass(frozen=True)
class VpnStatus:
    """GuestVpnStatus, as the agent reports it."""

    state: str
    last_error: str | None
    remote: str | None
    dns: list[str]
    exit_ip: str | None

    @property
    def activation_failed(self) -> bool:
        return self.state == "error"


def decode_upload(raw: bytes, field: str) -> str:
    """The exact text of an uploaded file.

    Strict UTF-8 and nothing else: no BOM removal, no newline conversion, no
    stripping. The agent validates and normalises the profile; doing part of
    that here would make what reaches NetworkManager differ from what the owner
    uploaded with no one saying so. A file that is not UTF-8 is refused rather
    than decoded with replacement characters.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise UploadNotText(field) from err


def _command(kind: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "type": kind,
        "feature": FEATURE,
        "action": action,
        "params": params,
    }


def build_import_command(
    ovpn: bytes, ca: bytes | None, username: str, password: str
) -> dict[str, Any]:
    """The `vpn.import_profile` command for these uploads and credentials.

    The credentials go as typed, in any script: a password is never trimmed.
    ``ca`` is left out entirely when the owner uploaded none -- the agent reads
    an absent key as "no separate CA", and a null as a malformed request.
    """
    params: dict[str, Any] = {
        "ovpn": decode_upload(ovpn, "ovpn"),
        "username": username,
        "password": password,
    }
    if ca is not None:
        params["ca"] = decode_upload(ca, "ca")
    return _command("action", "import_profile", params)


def build_status_command() -> dict[str, Any]:
    """The `vpn.get_status` query."""
    return _command("query", "get_status", {})


def auth_message(token: str) -> dict[str, str]:
    return {"type": "auth", "token": token}


def is_auth_required(message: Any) -> bool:
    return isinstance(message, dict) and message.get("type") == "auth_required"


def check_auth_result(message: Any) -> None:
    """Accept a successful auth_result; raise on a refusal or anything else."""
    if not isinstance(message, dict) or message.get("type") != "auth_result":
        raise ProtocolError("expected auth_result after the token")
    if message.get("success") is not True:
        raise AuthRejected(str(message.get("error") or "token refused"))


def reply_for(message: Any, command_id: str) -> tuple[bool, Any]:
    """Is ``message`` the reply to ``command_id``? ``(True, data)`` when it is.

    ``(False, None)`` for what the agent may legitimately send in between: the
    welcome that follows authentication, broadcast events, replies to other
    commands. A reply with id ``error`` is the agent saying it could not read
    the command at all; waiting for the command's own id would only end in a
    timeout, so it raises like a failed reply does.
    """
    if not isinstance(message, dict):
        raise ProtocolError("the agent sent a message that is not a JSON object")
    message_id = message.get("id")
    if message_id not in (command_id, "error"):
        return False, None
    if message.get("success") is True and message_id == command_id:
        return True, message.get("data")
    raise AgentError(str(message.get("error") or "the agent reported a failure"))


def close_error(code: int | None, reason: str) -> Exception:
    """What the agent closing the socket means, as the exception to raise."""
    if code == CLOSE_POLICY_VIOLATION:
        return AuthRejected(reason or "token refused")
    text = f"the agent closed the connection ({code if code is not None else 'no code'}"
    text += f": {reason})" if reason else ")"
    return AgentError(text)


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(f"status field {key} is not a string")
    return value


def parse_status(data: Any) -> VpnStatus:
    """A GuestVpnStatus from the agent, checked field by field."""
    if not isinstance(data, dict):
        raise ProtocolError("the status is not a JSON object")
    state = data.get("state")
    if state not in STATES:
        raise ProtocolError(f"unknown guest VPN state {state!r}")
    dns = data.get("dns", [])
    if not isinstance(dns, list) or not all(isinstance(d, str) for d in dns):
        raise ProtocolError("status field dns is not a list of strings")
    return VpnStatus(
        state=state,
        last_error=_optional_str(data, "lastError"),
        remote=_optional_str(data, "remote"),
        dns=dns,
        exit_ip=_optional_str(data, "exitIp"),
    )
