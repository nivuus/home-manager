"""Per-client MQTT accounts: the move from one shared password file to
Mosquitto's Dynamic Security plugin.

Design: docs/superpowers/specs/2026-11-09-mqtt-per-client-accounts-design.md

THE BROKER MUST ACCEPT TOMORROW EVERY LOGIN IT ACCEPTED YESTERDAY.

mosquitto/mosquitto.conf and mosquitto/passwd are installation data (rule 1
of install.py). The migration therefore follows three rules:

1. THE CONFIGURATION IS REWRITTEN ONLY WHEN IT IS RECOGNISED. Its directives
   (comments and blank lines ignored) must equal those of a version this
   package once laid down. Anything else was edited by its owner: it is left
   untouched and the hook says so -- out loud, never silently.

2. THE ACCOUNTS FILE IS CREATED, NEVER EDITED. Once the broker runs, the
   plugin owns dynamic-security.json and rewrites it after every change made
   with mosquitto_ctrl. The hook writes it only when it is absent, so it can
   never delete a client or a role an operator created.

3. EVERY EXISTING ACCOUNT IS CARRIED OVER WITH ITS HASH. The password file
   and the plugin share one hash decoder (libcommon/password_common.c in
   Mosquitto), so each "user:hash" line becomes a client whose
   encoded_password is that hash, verbatim. No plaintext is needed, and the
   accounts keep the full access they had, through the legacy-full-access
   role, until the operator replaces them with per-device clients.

The JSON is written BEFORE the configuration that loads it: a broker started
on the new configuration without its accounts would lock every client out.
"""
import base64
import hashlib
import json
import os
import secrets

CONF_REL = "mosquitto/mosquitto.conf"
PASSWD_REL = "mosquitto/passwd"
DYNSEC_REL = "mosquitto/dynamic-security.json"
CONF_BACKUP_REL = "mosquitto/mosquitto.conf.pre-dynsec"

# The broker runs as this uid in the eclipse-mosquitto image, and the plugin
# must be able to rewrite its file.
MOSQUITTO_UID = 1883

ADMIN_USERNAME = "nivuus-admin"
ADMIN_ENV_KEY = "MQTT_ADMIN_PASSWORD"
LEGACY_USERNAME = "mqtt"
# Mosquitto's own default is 1000 (PW_DEFAULT_ITERATIONS). The admin logs in
# a few times a year, so the hook can afford a far stronger hash.
ADMIN_ITERATIONS = 210000

# Hash prefixes mosquitto_pw_decode() understands: sha512, sha512-pbkdf2,
# argon2id.
KNOWN_HASH_PREFIXES = ("$6$", "$7$", "$argon2id$")

# The directives of every mosquitto.conf this package laid down before Dynamic
# Security. The configuration of an install is migrated only if it matches one
# of these exactly. Never remove an entry: an install that skipped a release
# still carries it.
LEGACY_CONFS = {
    # c4b7372, 2026-08-28: password file + per_listener_settings.
    "password-file": (
        "persistence true",
        "persistence_location /mosquitto/data/",
        "log_dest file /mosquitto/data/mosquitto.log",
        "log_type error",
        "log_type warning",
        "log_type notice",
        "log_type information",
        "log_type subscribe",
        "log_type unsubscribe",
        "per_listener_settings true",
        "listener 1883",
        "allow_anonymous false",
        "password_file /mosquitto/data/passwd",
        "listener 1884",
        "allow_anonymous false",
        "password_file /mosquitto/data/passwd",
        "listener 8883",
        "certfile /mosquitto/data/certs/server.crt",
        "keyfile /mosquitto/data/certs/server.key",
        "tls_version tlsv1.2",
        "require_certificate false",
        "allow_anonymous true",
        "acl_file /mosquitto/data/acl_meross",
        "listener 8884",
        "allow_anonymous false",
        "password_file /mosquitto/data/passwd",
        "user mosquitto",
    ),
}


def directives(text):
    """The directives of a mosquitto.conf: comments and blank lines dropped,
    whitespace collapsed. Mosquitto only treats a '#' at line start as a
    comment, so nothing else is stripped."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(" ".join(stripped.split()))
    return tuple(out)


def conf_state(text, template):
    """'current', 'legacy' or 'unknown' for an installed configuration."""
    found = directives(text)
    if found == directives(template):
        return "current"
    if found in LEGACY_CONFS.values():
        return "legacy"
    return "unknown"


def parse_passwd(text):
    """The (username, encoded hash) pairs of a Mosquitto password file.

    Raises ValueError on a line the plugin could not decode: carrying it over
    would create a client nobody can log in as, without an error anywhere.
    """
    accounts = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        username, sep, encoded = line.partition(":")
        if not sep or not username or not encoded.startswith(
                KNOWN_HASH_PREFIXES):
            raise ValueError(
                f"{PASSWD_REL} line {number}: not a hashed 'user:hash' entry "
                "(a plaintext file must be hashed with mosquitto_passwd -U "
                "first)")
        accounts.append((username, encoded.strip()))
    return accounts


def encode_pbkdf2(password, iterations=ADMIN_ITERATIONS, salt=None):
    """A password in Mosquitto's sha512-pbkdf2 encoding:
    $7$<iterations>$<base64 salt>$<base64 hash>, 64-byte salt and hash, as
    pw__encode_sha512_pbkdf2() writes it."""
    salt = secrets.token_bytes(64) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt,
                                 iterations, 64)
    return "$7${}${}${}".format(iterations,
                                base64.b64encode(salt).decode("ascii"),
                                base64.b64encode(digest).decode("ascii"))


def _acl(acltype, topic, allow=True, priority=0):
    return {"acltype": acltype, "topic": topic, "priority": priority,
            "allow": allow}


def _readwrite(topic):
    """Publish, receive, subscribe and unsubscribe on a topic tree."""
    return [_acl("publishClientSend", topic),
            _acl("publishClientReceive", topic),
            _acl("subscribePattern", topic),
            _acl("unsubscribePattern", topic)]


def _read(topic):
    return [_acl("publishClientReceive", topic),
            _acl("subscribePattern", topic),
            _acl("unsubscribePattern", topic)]


# One role per client kind. %u is the client's username, substituted by the
# plugin; it must fill a whole topic level. The default for publishing and
# subscribing is DENY: a topic a role does not name is closed to it -- in
# particular system_agent/#, whose command topics run as root on the host.
ROLES = (
    ("home-assistant", "Home Assistant: the whole application tree.",
     _readwrite("#")),
    ("zigbee2mqtt", "zigbee2mqtt: its base topic and Home Assistant "
     "discovery, except the system agent's discovery and HA's birth topic.",
     [_acl("publishClientSend", "homeassistant/+/nivuus/#", False, 10),
      _acl("publishClientSend", "homeassistant/status", False, 10)]
     + _readwrite("zigbee2mqtt/#") + _readwrite("homeassistant/#")),
    ("system-agent", "nivuus mqtt-system-agent: its tree, its discovery, "
     "HA's birth topic.",
     _readwrite("system_agent/#") + _readwrite("homeassistant/+/nivuus/#")
     + _read("homeassistant/status")),
    ("valetudo", "Valetudo robot: username = Valetudo identifier; its own "
     "tree and discovery only.",
     _readwrite("valetudo/%u/#") + _readwrite("homeassistant/+/%u/#")
     + _read("homeassistant/status")),
    ("fully-kiosk", "Fully Kiosk tablet: publishes events and device info, "
     "subscribes to nothing.",
     [_acl("publishClientSend", "fully/#")]),
    ("operator", "Interactive debugging (mosquitto_sub/pub): everything.",
     _readwrite("#") + _read("$SYS/#")),
    ("legacy-full-access", "Accounts carried over from the password file, "
     "with the unrestricted access they had. Remove once replaced.",
     _readwrite("#")),
    ("dynsec-admin", "Administers clients, groups and roles.",
     _readwrite("$CONTROL/dynamic-security/#")),
)


def build_dynsec(admin_encoded, accounts):
    """The initial dynamic-security.json, as a dict."""
    clients = [{
        "username": ADMIN_USERNAME,
        "textname": "Account administration (mosquitto_ctrl)",
        "encoded_password": admin_encoded,
        "roles": [{"rolename": "dynsec-admin"}],
    }]
    for username, encoded in accounts:
        clients.append({
            "username": username,
            "textname": "Carried over from the password file",
            "encoded_password": encoded,
            "roles": [{"rolename": "legacy-full-access"}],
        })
    return {
        "defaultACLAccess": {
            "publishClientSend": False,
            "publishClientReceive": True,
            "subscribe": False,
            "unsubscribe": True,
        },
        "clients": clients,
        "groups": [],
        "roles": [{"rolename": name, "textdescription": text, "acls": acls}
                  for name, text, acls in ROLES],
    }


def _read_text(path):
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return fh.read()


def plan(dest, template, existing_env, wizard_password):
    """Decide what the migration will do, before the install writes anything.

    `existing_env` maps the keys of the .env already in place. Raises
    ValueError when the accounts could not be carried over safely.
    """
    conf_text = _read_text(os.path.join(dest, CONF_REL))
    state = "absent" if conf_text is None else conf_state(conf_text, template)
    result = {"state": state, "rewrite_conf": state == "legacy",
              "dynsec": None, "admin_password": None}
    if state == "unknown":
        return result
    if os.path.lexists(os.path.join(dest, DYNSEC_REL)):
        return result

    passwd = _read_text(os.path.join(dest, PASSWD_REL))
    accounts = parse_passwd(passwd) if passwd is not None else []
    names = [name for name, _ in accounts]
    if ADMIN_USERNAME in names:
        raise ValueError(f"{PASSWD_REL} already has an account named "
                         f"{ADMIN_USERNAME!r}, the name of the admin client")
    # A fresh install has no password file: the wizard's answer, if any,
    # becomes the shared account the bootstrap configurations log in with.
    if wizard_password and LEGACY_USERNAME not in names:
        accounts.append((LEGACY_USERNAME, encode_pbkdf2(wizard_password)))

    admin_password = (existing_env.get(ADMIN_ENV_KEY)
                      or secrets.token_urlsafe(32))
    result["dynsec"] = build_dynsec(encode_pbkdf2(admin_password), accounts)
    result["admin_password"] = admin_password
    result["carried"] = names
    return result


def apply(dest, template, result):
    """Write what plan() decided. Returns the lines to report."""
    report = []
    if result["dynsec"] is not None:
        path = os.path.join(dest, DYNSEC_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(result["dynsec"], fh, indent=2)
            fh.write("\n")
        if os.geteuid() == 0:
            os.chown(tmp, MOSQUITTO_UID, MOSQUITTO_UID)
        else:
            # Not an error swallowed: a process that is not root cannot give
            # a file to another uid. The engine installs as root; an
            # unprivileged run (the CI idempotence gate, a developer
            # checkout) only gets the file. The image's entrypoint also
            # chowns /mosquitto/data to 1883 when the container starts.
            report.append(f"WARNING: {DYNSEC_REL} is not owned by "
                          f"{MOSQUITTO_UID}; ownership is set when install "
                          "runs as root")
        os.replace(tmp, path)
        report.append(f"{DYNSEC_REL} created: {ADMIN_USERNAME} + "
                      f"{len(result['carried'])} account(s) carried over")

    if result["rewrite_conf"]:
        conf = os.path.join(dest, CONF_REL)
        backup = os.path.join(dest, CONF_BACKUP_REL)
        with open(conf) as fh:
            old = fh.read()
        if not os.path.exists(backup):
            with open(backup, "w") as fh:
                fh.write(old)
        # Rewritten IN PLACE: the file is bind-mounted on its own into the
        # container, and a rename would leave the mount on the old inode.
        with open(conf, "w") as fh:
            fh.write(template)
        report.append(f"{CONF_REL} migrated to Dynamic Security "
                      f"(previous version in {CONF_BACKUP_REL}); restart "
                      "the mosquitto container to apply it")

    if result["state"] == "unknown":
        report.append(f"WARNING: {CONF_REL} was edited by hand and is left "
                      "untouched: per-client accounts are NOT enabled. See "
                      "docs/claude/mqtt-accounts.md to migrate it manually")
    return report
