#!/usr/bin/env python3
"""Does nivuus_vpn still speak the agent's WebSocket protocol?

The integration forwards the guest VPN profile, its optional CA and the
provider credentials to the mqtt system agent over its WebSocket API
(nivuus/mqtt, src/websocket/sessionProtocol.ts and
src/features/vpn/vpnWebSocketHandlers.ts). Nothing in Home Assistant checks
that wire format: a drift shows up as a form error on the owner's phone, or
worse, as a profile silently altered on its way to NetworkManager. Hence these
assertions, run without Home Assistant installed.

Run: python3 tests/test_vpn_payloads.py
"""
import json
import pathlib
import re
import sys
import uuid

# Do not seed a __pycache__ in the deployed subtree: install.py copies it as is
# to the target, and Home Assistant would load the stale cache.
sys.dont_write_bytecode = True

REPO = pathlib.Path(__file__).resolve().parents[1]
INTEGRATION = REPO / "stack" / "config" / "custom_components" / "nivuus_vpn"
sys.path.insert(0, str(INTEGRATION))

import const  # noqa: E402
import payloads  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def raises(label, exc_type, fn, *args):
    """Run fn(*args); return the exception if it is exc_type, else record a failure."""
    try:
        fn(*args)
    except exc_type as err:
        return err
    except Exception as err:  # noqa: BLE001 -- the wrong type IS the failure
        failures.append(f"{label}: raised {type(err).__name__}: {err}")
        return None
    failures.append(f"{label}: did not raise {exc_type.__name__}")
    return None


# --- the import command ------------------------------------------------------
# The .ovpn reaches the agent byte for byte: the agent owns the validation and
# the normalisation (BOM, CRLF, inline <ca>). Stripping or re-encoding here
# would change what the owner uploaded without anyone saying so.
OVPN = "client\r\nremote vpn.example.net 1194\r\n# serveur à Zürich, 東京\n"
CA = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
cmd = payloads.build_import_command(
    OVPN.encode("utf-8"), CA.encode("utf-8"), "owner", "pässwörd 密码 ")
check("the command targets the vpn feature", cmd["feature"], "vpn")
check("importing is an action", cmd["type"], "action")
check("the action is import_profile", cmd["action"], "import_profile")
check("build_import_command decodes UTF-8 and keeps exact text",
      cmd["params"]["ovpn"], OVPN)
check("the CA is decoded and kept exact", cmd["params"]["ca"], CA)
check("the username is forwarded as typed", cmd["params"]["username"], "owner")
# Any script, trailing space included: a password is never normalised.
check("the password is forwarded exactly, in any script",
      cmd["params"]["password"], "pässwörd 密码 ")
check("the params hold exactly the four agent fields",
      sorted(cmd["params"]), ["ca", "ovpn", "password", "username"])
check("the command id is a UUID string",
      str(uuid.UUID(cmd["id"])), cmd["id"])
check("two commands never share an id",
      payloads.build_import_command(b"a", None, "u", "p")["id"]
      != payloads.build_import_command(b"a", None, "u", "p")["id"], True)

# A BOM is part of the file: the agent strips it, not this side.
check("a UTF-8 BOM reaches the agent untouched",
      payloads.build_import_command(
          "﻿client\n".encode("utf-8"), None, "u", "p")["params"]["ovpn"],
      "﻿client\n")

check("omits ca when None",
      "ca" in payloads.build_import_command(b"client\n", None, "u", "p")["params"],
      False)

# The command never carries the agent token: the token goes once, in the auth
# message, and build_import_command does not even receive it.
serialised = json.dumps(payloads.build_import_command(b"client\n", None, "u", "p"))
check("never includes the token", "token" in serialised, False)

# --- files that are not UTF-8 text -------------------------------------------
# A .ovpn is text. A Latin-1 or binary file must be refused with a reason that
# names the file, never decoded with replacement characters behind the owner's
# back.
err = raises("rejects non-UTF-8 files with a clear error",
             payloads.UploadNotText, payloads.build_import_command,
             b"remote h\xe9te 1194\n", None, "u", "p")
if err is not None:
    check("the error names the profile field", err.field, "ovpn")
    check("the error says what is expected", "UTF-8" in str(err), True)

err = raises("a non-UTF-8 CA is refused too", payloads.UploadNotText,
             payloads.build_import_command, b"client\n", b"\xff\xfe\x00", "u", "p")
if err is not None:
    check("the error names the CA field", err.field, "ca")

# --- the status query --------------------------------------------------------
status_cmd = payloads.build_status_command()
check("the status is a query", status_cmd["type"], "query")
check("the status query targets vpn.get_status",
      (status_cmd["feature"], status_cmd["action"], status_cmd["params"]),
      ("vpn", "get_status", {}))

# --- the handshake -----------------------------------------------------------
check("the auth message", payloads.auth_message("t0k"),
      {"type": "auth", "token": "t0k"})
check("auth_required is recognised",
      payloads.is_auth_required({"type": "auth_required", "message": "…"}), True)
check("a welcome is not auth_required",
      payloads.is_auth_required({"id": "welcome", "success": True}), False)

check("a successful auth_result passes",
      payloads.check_auth_result({"type": "auth_result", "success": True}), None)
raises("a refused auth_result raises AuthRejected", payloads.AuthRejected,
       payloads.check_auth_result,
       {"type": "auth_result", "success": False, "error": "Invalid token"})
raises("anything else in place of auth_result is a protocol error",
       payloads.ProtocolError, payloads.check_auth_result, {"id": "welcome"})

# --- the reply ---------------------------------------------------------------
CID = "6f1c1d2e-0000-4000-8000-000000000001"
check("the welcome that follows auth is not the reply",
      payloads.reply_for({"id": "welcome", "success": True, "data": {}}, CID),
      (False, None))
check("a broadcast event is not the reply",
      payloads.reply_for({"type": "event", "feature": "vpn", "event": "x",
                          "data": {}, "timestamp": 1}, CID),
      (False, None))
check("a reply to another command is not this one",
      payloads.reply_for({"id": "other", "success": True, "data": 1}, CID),
      (False, None))
check("the matching success returns its data",
      payloads.reply_for({"id": CID, "success": True, "data": {"state": "x"}}, CID),
      (True, {"state": "x"}))

err = raises("a matching failure raises AgentError", payloads.AgentError,
             payloads.reply_for,
             {"id": CID, "success": False, "error": "Invalid ovpn parameter"}, CID)
if err is not None:
    check("the agent's reason is kept for the form",
          err.reason, "Invalid ovpn parameter")

# The agent answers id "error" when it could not even parse the command: the
# command never ran, and waiting for its id would only end in a timeout.
err = raises("an id-less protocol error from the agent raises", payloads.AgentError,
             payloads.reply_for,
             {"id": "error", "success": False, "error": "Unknown message type"}, CID)
if err is not None:
    check("its reason is kept", err.reason, "Unknown message type")

raises("a reply that is not an object is a protocol error",
       payloads.ProtocolError, payloads.reply_for, ["x"], CID)
check("a ProtocolError is an AgentError (shown with its reason)",
      issubclass(payloads.ProtocolError, payloads.AgentError), True)

# --- the agent closing the socket --------------------------------------------
# 1008 is the agent's answer to a wrong token; anything else (1009: the frame
# exceeded max_payload_bytes) is the agent refusing, and must reach the owner
# with its code rather than as a vague "cannot connect".
check("1008 is a refused token",
      isinstance(payloads.close_error(1008, "Authentication failed"),
                 payloads.AuthRejected), True)
closed = payloads.close_error(1009, "Max payload size exceeded")
check("another close code is an agent error",
      isinstance(closed, payloads.AgentError), True)
check("the close code and reason are named",
      "1009" in closed.reason and "Max payload size exceeded" in closed.reason,
      True)
check("a close without a code still says the connection closed",
      "closed" in payloads.close_error(None, "").reason, True)

# --- the status --------------------------------------------------------------
status = payloads.parse_status({"state": "error", "lastError": "TLS failed",
                                "dns": ["10.8.0.1"]})
check("the state is read", status.state, "error")
check("lastError is read", status.last_error, "TLS failed")
check("dns is read", status.dns, ["10.8.0.1"])
check("absent optional fields are None",
      (status.remote, status.exit_ip), (None, None))
check("a connected status carries its exit IP",
      payloads.parse_status({"state": "connected", "dns": [],
                             "exitIp": "203.0.113.7",
                             "remote": "vpn.example.net"}).exit_ip,
      "203.0.113.7")
raises("a status without a known state is a protocol error",
       payloads.ProtocolError, payloads.parse_status, {"state": "sleeping", "dns": []})
raises("a status that is not an object is a protocol error",
       payloads.ProtocolError, payloads.parse_status, "connected")

check("only error means the activation failed",
      [payloads.parse_status({"state": s, "dns": []}).activation_failed
       for s in ("connected", "connecting", "disabled", "no_config", "error")],
      [False, False, False, False, True])

# --- manifest and translations -----------------------------------------------
manifest = json.loads((INTEGRATION / "manifest.json").read_text())
check("the manifest names the domain", manifest["domain"], const.DOMAIN)
check("the manifest depends on file_upload",
      "file_upload" in manifest["dependencies"], True)
check("the integration has a config flow", manifest["config_flow"], True)

strings = json.loads((INTEGRATION / "strings.json").read_text())
en = json.loads((INTEGRATION / "translations" / "en.json").read_text())
fr = json.loads((INTEGRATION / "translations" / "fr.json").read_text())
check("en.json is strings.json", en, strings)


def leaves(tree, prefix=()):
    """Every (path, text) leaf of a translation tree."""
    if isinstance(tree, dict):
        for key, value in tree.items():
            yield from leaves(value, prefix + (key,))
    else:
        yield prefix, tree


def placeholders(text):
    return sorted(re.findall(r"\{(\w+)\}", text))


en_leaves = dict(leaves(en))
fr_leaves = dict(leaves(fr))
check("fr.json has exactly the keys of en.json",
      sorted(fr_leaves), sorted(en_leaves))
for path, text in en_leaves.items():
    if path in fr_leaves:
        check(f"{'.'.join(path)} keeps its placeholders in French",
              placeholders(fr_leaves[path]), placeholders(text))

# Every error key the flows can set must be translated, or the owner sees a
# raw key on the form.
for section, keys in (("config", const.CONFIG_ERRORS),
                      ("options", const.OPTIONS_ERRORS)):
    for key in keys:
        check(f"{section}.error.{key} is translated",
              key in strings[section]["error"], True)

# The reason the agent gave is the only useful part of these errors: the text
# must show it.
for section, key in (("config", const.ERROR_AGENT),
                     ("options", const.ERROR_AGENT),
                     ("options", const.ERROR_ACTIVATION)):
    check(f"{section}.error.{key} shows the agent's reason",
          placeholders(strings[section]["error"][key]), ["reason"])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_vpn_payloads: OK")
