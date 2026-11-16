"""The broker clients this package knows by name, and how each one receives
its password.

A fresh install has no shared account. hooks/mqtt_accounts.py gives each
client below its own Dynamic Security client, limited to its role, and draws
its password. The passwords are saved in .env (root, 0600) BEFORE the accounts
file is written, like the admin secret, so an interrupted run can be resumed
with the same values. deliver() then puts each password where its client
reads it:

- Home Assistant: nowhere. Its MQTT integration is configured in its own
  interface; the hook says which key of .env holds the password.
- zigbee2mqtt: zigbee2mqtt/mqtt-credentials.yaml, referenced by the packaged
  configuration as '!mqtt-credentials.yaml password' (zigbee2mqtt resolves
  such a reference against its data directory, whatever the file name).
- the nivuus mqtt-system-agent: MQTT_USERNAME and MQTT_PASSWORD in
  /etc/mqtt-system-agent.env, the environment file its systemd unit reads
  (nivuus/mqtt, debian/mqtt-system-agent.service). The file is written even
  when the agent is not installed yet: the accounts file is created once, so
  the hand-over cannot depend on which package was installed first.

Devices the package cannot know (robots, tablets) get their client from the
operator, with mosquitto_ctrl: docs/claude/mqtt-accounts.md.
"""
import collections
import json
import os

import safe_files

Client = collections.namedtuple("Client", "username role textname env_key")

HOME_ASSISTANT = Client("homeassistant", "home-assistant", "Home Assistant",
                        "MQTT_HOMEASSISTANT_PASSWORD")
ZIGBEE2MQTT = Client("zigbee2mqtt", "zigbee2mqtt", "zigbee2mqtt",
                     "MQTT_ZIGBEE2MQTT_PASSWORD")
SYSTEM_AGENT = Client("mqtt-system-agent", "system-agent",
                      "nivuus mqtt-system-agent", "MQTT_SYSTEM_AGENT_PASSWORD")
CLIENTS = (HOME_ASSISTANT, ZIGBEE2MQTT, SYSTEM_AGENT)

Z2M_CONF_REL = "zigbee2mqtt/configuration.yaml"
Z2M_CREDENTIALS_REL = "zigbee2mqtt/mqtt-credentials.yaml"
# Relative to the installation root, not to the deployment directory.
AGENT_ENV_REL = "etc/mqtt-system-agent.env"
AGENT_ENV_KEYS = ("MQTT_USERNAME", "MQTT_PASSWORD")


def assignments(text):
    """The KEY=value lines of an environment file, values verbatim.

    Only used to know which keys a file defines and whether a value is the
    one this hook wrote: quoting and escapes are not interpreted.
    """
    found = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped[0] not in "#;" and "=" in stripped:
            key, _, value = stripped.partition("=")
            found[key.strip()] = value.strip()
    return found


def require_safe_targets(root, dest):
    """Refuse, before the first write of the phase, a symbolic link planted
    where a password is about to be written."""
    safe_files.require_regular_or_absent(
        os.path.join(dest, Z2M_CREDENTIALS_REL))
    safe_files.require_regular_or_absent(os.path.join(root, AGENT_ENV_REL))


def _deliver_zigbee2mqtt(dest, env, rendered_now):
    path = os.path.join(dest, Z2M_CREDENTIALS_REL)
    if os.path.lexists(path):
        # zigbee2mqtt rewrites this file when its settings change, and the
        # operator may have put another client's password there.
        return []
    password = env.get(ZIGBEE2MQTT.env_key)
    if password:
        safe_files.require_plain_dir(os.path.dirname(path))
        # JSON quoting is valid YAML and keeps any token a string.
        safe_files.create_exclusive(
            path, f"password: {json.dumps(password)}\n", 0o600)
        return [f"{Z2M_CREDENTIALS_REL} created: password of the "
                f"{ZIGBEE2MQTT.username} broker client"]
    if Z2M_CONF_REL in rendered_now:
        return [f"WARNING: {Z2M_CONF_REL} was created and logs in to the "
                f"broker as {ZIGBEE2MQTT.username} with the password of "
                f"{Z2M_CREDENTIALS_REL}, which does not exist: the accounts "
                "file was not created by this hook. Create that client and "
                "write its password there (docs/claude/mqtt-accounts.md)"]
    return []


def _deliver_system_agent(root, env):
    password = env.get(SYSTEM_AGENT.env_key)
    if not password:
        return []
    path = os.path.join(root, AGENT_ENV_REL)
    text = safe_files.read_text(path) or ""
    ours = dict(zip(AGENT_ENV_KEYS, (SYSTEM_AGENT.username, password)))
    have = assignments(text)
    if not set(ours) & set(have):
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(f"{key}={value}\n" for key, value in ours.items())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 0600 whatever it was: the file carries secrets.
        safe_files.write_atomic(path, text, 0o600)
        return [f"/{AGENT_ENV_REL}: broker credentials of the "
                f"{SYSTEM_AGENT.username} client added; restart "
                "mqtt-system-agent to apply them"]
    if all(have.get(key) == value for key, value in ours.items()):
        return []
    return [f"/{AGENT_ENV_REL} already defines "
            f"{' or '.join(AGENT_ENV_KEYS)} and is left untouched: the "
            f"agent logs in with those, not as {SYSTEM_AGENT.username} "
            f"(its password is {SYSTEM_AGENT.env_key} in .env)"]


def deliver(root, dest, env, rendered_now):
    """Hand each provisioned client its password. Returns report lines.

    `env` maps the keys of .env as it stands after this run's secrets were
    saved; `rendered_now` lists the templates this run created. Nothing
    existing is overwritten, and a host whose .env holds none of the client
    secrets (accounts created by hand) is left exactly as it is.
    """
    return (_deliver_zigbee2mqtt(dest, env, rendered_now)
            + _deliver_system_agent(root, env))
