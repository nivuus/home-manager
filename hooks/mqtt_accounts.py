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

import safe_files

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


def _existing_usernames(path):
    """The usernames of an accounts file already on disk."""
    try:
        doc = json.loads(safe_files.read_text(path))
        return {client["username"] for client in doc["clients"]}
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, safe_files.UnsafePath):
            raise
        raise ValueError(f"{DYNSEC_REL} exists but cannot be read as a "
                         f"Dynamic Security file ({exc})") from exc


def plan(dest, template, existing_env, wizard_password):
    """Decide what the migration will do, before the install writes anything.

    `existing_env` maps the keys of the .env already in place. Raises
    ValueError when the migration could lock a client out or would have to
    follow a symbolic link planted in the broker's directory.
    """
    safe_files.require_plain_dir(os.path.join(dest, "mosquitto"))
    conf_text = safe_files.read_text(os.path.join(dest, CONF_REL))
    state = "absent" if conf_text is None else conf_state(conf_text, template)
    result = {"state": state, "rewrite_conf": state == "legacy",
              "dynsec": None, "admin_password": None, "admin_is_new": False}
    if state == "unknown":
        return result
    if state == "legacy":
        safe_files.require_regular_or_absent(
            os.path.join(dest, CONF_BACKUP_REL))

    dynsec_path = os.path.join(dest, DYNSEC_REL)
    safe_files.require_regular_or_absent(dynsec_path)
    exists = os.path.lexists(dynsec_path)
    if exists and state == "current":
        # The broker already runs on this file and owns it: an account the
        # operator deleted on purpose (the last step of the procedure
        # deletes "mqtt") must not be required back.
        return result

    passwd = safe_files.read_text(os.path.join(dest, PASSWD_REL))
    accounts = parse_passwd(passwd) if passwd is not None else []
    names = [name for name, _ in accounts]

    if exists:
        # A Dynamic Security configuration is about to be put on disk over
        # an accounts file the hook did not write: every login the password
        # file accepts today must still be accepted, and the admin must
        # exist to manage the rest.
        have = _existing_usernames(dynsec_path)
        missing = [n for n in [ADMIN_USERNAME] + names if n not in have]
        if missing:
            raise ValueError(
                f"{DYNSEC_REL} lacks {', '.join(missing)}: switching "
                f"{CONF_REL} to Dynamic Security would lock them out. Move "
                "the file away so it is recreated from the password file, "
                "or add them to it (see docs/superpowers/specs/"
                "2026-11-09-mqtt-per-client-accounts-design.md, Rollback), "
                "then re-run")
        return result

    if ADMIN_USERNAME in names:
        raise ValueError(f"{PASSWD_REL} already has an account named "
                         f"{ADMIN_USERNAME!r}, the name of the admin client")
    # A fresh install has no password file: the wizard's answer, if any,
    # becomes the shared account the bootstrap configurations log in with.
    if wizard_password and LEGACY_USERNAME not in names:
        accounts.append((LEGACY_USERNAME, encode_pbkdf2(wizard_password)))

    saved = existing_env.get(ADMIN_ENV_KEY)
    admin_password = saved or secrets.token_urlsafe(32)
    result["dynsec"] = build_dynsec(encode_pbkdf2(admin_password), accounts)
    result["admin_password"] = admin_password
    result["admin_is_new"] = not saved
    result["carried"] = names
    return result


def write_accounts(dest, result):
    """Create the accounts file plan() decided on. Returns report lines.

    The caller must have saved the admin secret before, and must not put a
    Dynamic Security configuration on disk until this has returned.
    """
    if result["dynsec"] is None:
        return []
    data = os.path.join(dest, "mosquitto")
    os.makedirs(data, exist_ok=True)
    safe_files.require_plain_dir(data)
    text = json.dumps(result["dynsec"], indent=2) + "\n"
    owned = safe_files.write_atomic(os.path.join(dest, DYNSEC_REL), text,
                                    0o600, (MOSQUITTO_UID, MOSQUITTO_UID))
    report = [f"{DYNSEC_REL} created: {ADMIN_USERNAME} + "
              f"{len(result['carried'])} account(s) carried over"]
    if not owned:
        # Not an error swallowed: a process that is not root cannot give a
        # file to another uid. The engine installs as root; an unprivileged
        # run (the CI idempotence gate, a developer checkout) only gets the
        # file. The image's entrypoint also chowns /mosquitto/data to 1883.
        report.append(f"WARNING: {DYNSEC_REL} is not owned by "
                      f"{MOSQUITTO_UID}; ownership is set when install runs "
                      "as root")
    return report


def migrate_conf(dest, template, result):
    """Switch a recognised legacy configuration to the template, once its
    backup is durable on disk. Returns report lines."""
    if result["state"] == "unknown":
        return [f"WARNING: {CONF_REL} was edited by hand and is left "
                "untouched: per-client accounts are NOT enabled. See "
                "docs/claude/mqtt-accounts.md to migrate it manually"]
    if not result["rewrite_conf"]:
        return []
    conf = os.path.join(dest, CONF_REL)
    backup = os.path.join(dest, CONF_BACKUP_REL)
    old = safe_files.read_text(conf)
    # An existing backup holds the configuration from before the FIRST
    # migration: it is kept. plan() refused anything but a regular file.
    if not os.path.lexists(backup):
        safe_files.create_exclusive(backup, old, 0o644)
    # In place: the file is bind-mounted alone into the container, and a
    # rename would leave the mount on the old inode.
    safe_files.rewrite_in_place(conf, template)
    return [f"{CONF_REL} migrated to Dynamic Security (previous version in "
            f"{CONF_BACKUP_REL}); recreate the mosquitto container to apply "
            "it"]

