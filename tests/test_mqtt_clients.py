#!/usr/bin/env python3
"""A fresh install wires every client it knows to its OWN broker account.

No shared account, no default password: zigbee2mqtt, Home Assistant and the
nivuus mqtt-system-agent each get a client limited to its role, and each
receives its password where it reads it. An existing install -- accounts
carried over, or an accounts file the broker already owns -- is left exactly
as it is.

Run: python3 tests/test_mqtt_clients.py
"""
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hooks"))
import mqtt_accounts as ma  # noqa: E402
import mqtt_clients as mc  # noqa: E402

HOOK = REPO / "hooks" / "install.py"
DEST_REL = "opt/nivuus/home-manager"
TEMPLATE = (REPO / "stack" / "mosquitto" / "mosquitto.conf").read_text()
LEGACY = "\n".join(ma.LEGACY_CONFS["password-file"]) + "\n"
HASH = ("$7$101$" + base64.b64encode(b"s" * 12).decode()
        + "$" + base64.b64encode(b"h" * 64).decode())

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def run(root):
    return subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root",
         str(root)], input=json.dumps({"answers": {}}), capture_output=True,
        text=True)


def verify(encoded, password):
    """Check a $7$ hash the way Mosquitto's pw__verify_sha512_pbkdf2 does."""
    _, _, iterations, salt, digest = encoded.split("$")
    return hashlib.pbkdf2_hmac(
        "sha512", password.encode(), base64.b64decode(salt),
        int(iterations), 64) == base64.b64decode(digest)


def env_file(path):
    return mc.assignments(path.read_text()) if path.exists() else {}


def clients(dest):
    doc = json.loads((dest / "mosquitto" / "dynamic-security.json")
                     .read_text())
    return {c["username"]: c for c in doc["clients"]}


def mode(path):
    return oct(path.stat().st_mode & 0o777)


# --- fresh install: one client per known client, each wired ---------------
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    dest = root / DEST_REL
    proc = run(root)
    check("fresh install succeeds", proc.returncode, 0)
    found = clients(dest)
    env = env_file(dest / ".env")

    check("exactly the admin and the known clients exist", sorted(found),
          sorted([ma.ADMIN_USERNAME] + [c.username for c in mc.CLIENTS]))
    for client in mc.CLIENTS:
        check(f"{client.username} holds its role and no other",
              found[client.username]["roles"], [{"rolename": client.role}])
        check(f"{client.username} logs in with the secret saved in .env",
              verify(found[client.username]["encoded_password"],
                     env.get(client.env_key, "")), True)
    check("every client has its own password",
          len({env[c.env_key] for c in mc.CLIENTS}), len(mc.CLIENTS))
    check(".env is root-only", mode(dest / ".env"), "0o600")

    # zigbee2mqtt: the packaged configuration, read the way zigbee2mqtt
    # reads it -- '!<file> <key>' resolved against its data directory.
    z2m = yaml.safe_load((dest / mc.Z2M_CONF_REL).read_text())["mqtt"]
    check("zigbee2mqtt logs in as its own client", z2m["user"],
          mc.ZIGBEE2MQTT.username)
    ref, _, key = z2m["password"].partition(" ")
    check("its password is a reference, not a value", ref[:1], "!")
    secret = dest / "zigbee2mqtt" / ref[1:]
    check("the referenced file is the one the hook wrote",
          secret, dest / mc.Z2M_CREDENTIALS_REL)
    check("the reference resolves to the client's password",
          verify(found[mc.ZIGBEE2MQTT.username]["encoded_password"],
                 str(yaml.safe_load(secret.read_text())[key])), True)
    check("zigbee2mqtt's credentials file is root-only", mode(secret),
          "0o600")

    # The system agent: the environment file its systemd unit reads.
    agent_env = root / mc.AGENT_ENV_REL
    agent = env_file(agent_env)
    check("the agent's environment file names its client",
          agent.get("MQTT_USERNAME"), mc.SYSTEM_AGENT.username)
    check("and carries the password that client verifies with",
          verify(found[mc.SYSTEM_AGENT.username]["encoded_password"],
                 agent.get("MQTT_PASSWORD", "")), True)
    check("the agent's environment file is root-only", mode(agent_env),
          "0o600")

    # Home Assistant is configured in its interface: the hook says where.
    check("the hook says where Home Assistant's password is",
          mc.HOME_ASSISTANT.env_key in proc.stdout, True)
    check("no password is printed",
          [k for k in (c.env_key for c in mc.CLIENTS)
           if env[k] in proc.stdout + proc.stderr], [])

    watched = (dest / ".env", dest / "mosquitto" / "dynamic-security.json",
               secret, agent_env, dest / mc.Z2M_CONF_REL)
    before = {p: p.read_bytes() for p in watched}
    check("a second pass succeeds", run(root).returncode, 0)
    check("a second pass rewrites none of it",
          {p: p.read_bytes() for p in watched}, before)

# --- the agent's environment file already holds other secrets -------------
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    agent_env = root / mc.AGENT_ENV_REL
    agent_env.parent.mkdir(parents=True)
    agent_env.write_text("# wizard channel\nNIVUUS_WS_TOKEN=keep-me")
    agent_env.chmod(0o644)
    check("install over an existing environment file succeeds",
          run(root).returncode, 0)
    text = agent_env.read_text()
    check("what the file held is kept, verbatim, first",
          text.startswith("# wizard channel\nNIVUUS_WS_TOKEN=keep-me\n"),
          True)
    check("the broker credentials are added",
          env_file(agent_env).get("MQTT_USERNAME"),
          mc.SYSTEM_AGENT.username)
    check("a file that now carries a secret is closed to others",
          mode(agent_env), "0o600")

# --- the agent already has broker credentials of its own ------------------
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    agent_env = root / mc.AGENT_ENV_REL
    agent_env.parent.mkdir(parents=True)
    own = "MQTT_USERNAME=mine\nMQTT_PASSWORD='quoted value'\n"
    agent_env.write_text(own)
    proc = run(root)
    check("existing agent credentials do not fail the phase",
          proc.returncode, 0)
    check("they are left byte for byte", agent_env.read_text(), own)
    check("and the hook says the agent does not use its new client",
          "left untouched" in proc.stdout, True)

# --- interrupted between the accounts file and the hand-over --------------
# The stack copy fails after the accounts file was written (a file sits
# where a directory of the stack goes, outside what the hook reads first). The re-run finds an accounts file it
# must not touch: the clients must still be wired, with the saved secrets.
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    dest = root / DEST_REL
    dest.mkdir(parents=True)
    (dest / "stellantis-worker").write_text("in the way")
    proc = run(root)
    check("a failed copy fails the phase", proc.returncode != 0, True)
    check("the accounts file was already written",
          (dest / "mosquitto" / "dynamic-security.json").is_file(), True)
    saved = env_file(dest / ".env")
    check("every client secret was saved BEFORE the accounts file",
          sorted(k for k in saved if k.startswith("MQTT_")),
          sorted([ma.ADMIN_ENV_KEY] + [c.env_key for c in mc.CLIENTS]))
    check("nothing was handed over yet",
          (root / mc.AGENT_ENV_REL).exists(), False)

    (dest / "stellantis-worker").unlink()
    accounts = (dest / "mosquitto" / "dynamic-security.json").read_bytes()
    check("the re-run succeeds", run(root).returncode, 0)
    check("the re-run does not rewrite the accounts file",
          (dest / "mosquitto" / "dynamic-security.json").read_bytes(),
          accounts)
    found = clients(dest)
    check("the re-run hands zigbee2mqtt the saved password",
          verify(found[mc.ZIGBEE2MQTT.username]["encoded_password"],
                 str(yaml.safe_load((dest / mc.Z2M_CREDENTIALS_REL)
                                    .read_text())["password"])), True)
    check("the re-run hands the agent the saved password",
          verify(found[mc.SYSTEM_AGENT.username]["encoded_password"],
                 env_file(root / mc.AGENT_ENV_REL).get("MQTT_PASSWORD", "")),
          True)

# --- a recreated accounts file reuses the saved client secrets ------------
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    dest = root / DEST_REL
    run(root)
    first = env_file(dest / ".env")
    (dest / "mosquitto" / "dynamic-security.json").unlink()
    check("recreating the accounts file succeeds", run(root).returncode, 0)
    check("no secret is redrawn", env_file(dest / ".env"), first)
    check("the recreated client verifies with the delivered password",
          verify(clients(dest)[mc.ZIGBEE2MQTT.username]["encoded_password"],
                 str(yaml.safe_load((dest / mc.Z2M_CREDENTIALS_REL)
                                    .read_text())["password"])), True)

# --- an existing install is left exactly as it is -------------------------
# Accounts carried over from the password file: which device logs in as
# what is the operator's knowledge. Nothing is created, nothing handed over.
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    dest = root / DEST_REL
    (dest / "mosquitto").mkdir(parents=True)
    (dest / "zigbee2mqtt").mkdir(parents=True)
    (dest / "mosquitto" / "mosquitto.conf").write_text(LEGACY)
    (dest / "mosquitto" / "passwd").write_text(f"mqtt:{HASH}\n")
    live = "mqtt:\n  user: mqtt\n  password: owner\nadvanced:\n  pan_id: 1\n"
    (dest / mc.Z2M_CONF_REL).write_text(live)
    check("a migration succeeds", run(root).returncode, 0)
    check("zigbee2mqtt's live configuration is untouched",
          (dest / mc.Z2M_CONF_REL).read_text(), live)
    check("no credentials file is created for zigbee2mqtt",
          (dest / mc.Z2M_CREDENTIALS_REL).exists(), False)
    check("the agent's environment file is not created",
          (root / mc.AGENT_ENV_REL).exists(), False)
    check("no client secret is drawn",
          [c.env_key for c in mc.CLIENTS
           if c.env_key in env_file(dest / ".env")], [])

# The broker already runs on its own accounts file (the reference host).
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    dest = root / DEST_REL
    (dest / "mosquitto").mkdir(parents=True)
    (dest / "zigbee2mqtt").mkdir(parents=True)
    (dest / "mosquitto" / "mosquitto.conf").write_text(TEMPLATE)
    owned = json.dumps({"clients": [{"username": ma.ADMIN_USERNAME},
                                    {"username": "zigbee2mqtt"}],
                        "roles": []}) + "\n"
    (dest / "mosquitto" / "dynamic-security.json").write_text(owned)
    (dest / mc.Z2M_CONF_REL).write_text("mqtt:\n  user: zigbee2mqtt\n")
    agent_env = root / mc.AGENT_ENV_REL
    agent_env.parent.mkdir(parents=True)
    agent_env.write_text("NIVUUS_WS_TOKEN=t\n")
    check("an update of a per-client install succeeds",
          run(root).returncode, 0)
    check("the broker's accounts file is untouched",
          (dest / "mosquitto" / "dynamic-security.json").read_text(), owned)
    check("the agent's environment file is untouched",
          agent_env.read_text(), "NIVUUS_WS_TOKEN=t\n")
    check("no credentials file appears next to zigbee2mqtt",
          (dest / mc.Z2M_CREDENTIALS_REL).exists(), False)

# A fresh zigbee2mqtt configuration over accounts the hook did not create:
# it references a password nobody wrote, and the hook must say so.
with tempfile.TemporaryDirectory() as root:
    root = pathlib.Path(root)
    dest = root / DEST_REL
    (dest / "mosquitto").mkdir(parents=True)
    (dest / "mosquitto" / "mosquitto.conf").write_text(TEMPLATE)
    (dest / "mosquitto" / "dynamic-security.json").write_text(
        json.dumps({"clients": [{"username": ma.ADMIN_USERNAME}],
                    "roles": []}))
    proc = run(root)
    check("it does not fail the phase", proc.returncode, 0)
    check("the missing credentials file is reported on stderr",
          mc.Z2M_CREDENTIALS_REL in proc.stderr, True)

# --- a link planted where the agent's environment file goes ---------------
with tempfile.TemporaryDirectory() as root, \
        tempfile.TemporaryDirectory() as outside:
    root = pathlib.Path(root)
    target = pathlib.Path(outside) / "victim"
    target.write_text("untouched\n")
    agent_env = root / mc.AGENT_ENV_REL
    agent_env.parent.mkdir(parents=True)
    agent_env.symlink_to(target)
    proc = run(root)
    check("a linked environment file is refused", proc.returncode, 1)
    check("nothing is written through the link", target.read_text(),
          "untouched\n")
    check("nothing is created next to its target",
          sorted(os.listdir(outside)), ["victim"])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_mqtt_clients: OK")
