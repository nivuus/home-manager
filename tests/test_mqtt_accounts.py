#!/usr/bin/env python3
"""The move to per-client MQTT accounts must not lock a single client out,
must never rewrite a configuration its owner edited, and must never touch an
accounts file the broker already owns.

These are the file-level guarantees. That Mosquitto accepts the files and
enforces the roles is proven against the real broker by
tests/broker_dynsec_container.py (make test-broker).

Run: python3 tests/test_mqtt_accounts.py
"""
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hooks"))
import mqtt_accounts as ma  # noqa: E402

HOOK = REPO / "hooks" / "install.py"
DEST_REL = "opt/nivuus/home-manager"
TEMPLATE = (REPO / "stack" / "mosquitto" / "mosquitto.conf").read_text()
LEGACY = ("# Mosquitto Configuration\n# hand-written comment\n\n"
          + "\n".join("  " + d for d in ma.LEGACY_CONFS["password-file"])
          + "\n")
HASH = ("$7$101$" + base64.b64encode(b"s" * 12).decode()
        + "$" + base64.b64encode(b"h" * 64).decode())

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def verify(encoded, password):
    """Check a $7$ hash the way Mosquitto's pw__verify_sha512_pbkdf2 does."""
    _, kind, iterations, salt, digest = encoded.split("$")
    assert kind == "7"
    return hashlib.pbkdf2_hmac(
        "sha512", password.encode(), base64.b64decode(salt),
        int(iterations), 64) == base64.b64decode(digest)


def topic_matches(filter_, topic):
    """MQTT filter matching, for the checks on the role table."""
    f, t = filter_.split("/"), topic.split("/")
    for i, level in enumerate(f):
        if level == "#":
            return True
        if i >= len(t) or (level != "+" and level != t[i]):
            return False
    return len(f) == len(t)


def run(root, answers=None):
    ctx = json.dumps({"answers": answers or {}})
    return subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root",
         str(root)], input=ctx, capture_output=True, text=True)


def install(root, conf=None, passwd=None, dynsec=None, env=None):
    data = pathlib.Path(root) / DEST_REL / "mosquitto"
    data.mkdir(parents=True)
    for name, content in (("mosquitto.conf", conf), ("passwd", passwd),
                          ("dynamic-security.json", dynsec)):
        if content is not None:
            (data / name).write_text(content)
    if env is not None:
        (pathlib.Path(root) / DEST_REL / ".env").write_text(env)
    return data


def env_admin(root):
    for line in (pathlib.Path(root) / DEST_REL / ".env").read_text() \
            .splitlines():
        if line.startswith(ma.ADMIN_ENV_KEY + "="):
            return line.partition("=")[2]
    return None


def clients(data):
    doc = json.loads((data / "dynamic-security.json").read_text())
    return {c["username"]: c for c in doc["clients"]}


# --- recognising a configuration ------------------------------------------
check("the template is current", ma.conf_state(TEMPLATE, TEMPLATE), "current")
check("the 2026-08-28 template, recommented and reindented, is legacy",
      ma.conf_state(LEGACY, TEMPLATE), "legacy")
check("one extra directive makes it unknown",
      ma.conf_state(LEGACY + "max_connections 10\n", TEMPLATE), "unknown")
check("one directive changed makes it unknown",
      ma.conf_state(LEGACY.replace("tls_version tlsv1.2",
                                   "tls_version tlsv1.3"), TEMPLATE),
      "unknown")

# --- the password file ----------------------------------------------------
check("hashed lines are carried verbatim",
      ma.parse_passwd(f"mqtt:{HASH}\n\nother:$argon2id$v=19$x\n"),
      [("mqtt", HASH), ("other", "$argon2id$v=19$x")])
for bad in ("mqtt:plaintext\n", "nocolon\n", ":$7$x\n"):
    try:
        ma.parse_passwd(bad)
        failures.append(f"{bad!r} should be refused")
    except ValueError:
        pass

# --- the hash the hook draws ----------------------------------------------
encoded = ma.encode_pbkdf2("s3cret", 1000, b"\x01" * 64)
check("$7$ encoding: 64-byte salt, 64-byte hash",
      [len(base64.b64decode(p)) for p in encoded.split("$")[3:]], [64, 64])
check("$7$ encoding verifies like Mosquitto", verify(encoded, "s3cret"), True)
check("a wrong password does not verify", verify(encoded, "other"), False)

# --- the role table -------------------------------------------------------
doc = ma.build_dynsec(encoded, [("mqtt", HASH)])
roles = {r["rolename"]: r["acls"] for r in doc["roles"]}
check("role names are unique", len(roles), len(doc["roles"]))
for client in doc["clients"]:
    for role in client["roles"]:
        check(f"{client['username']} has a defined role",
              role["rolename"] in roles, True)
check("publishing is denied by default",
      doc["defaultACLAccess"]["publishClientSend"], False)
check("subscribing is denied by default",
      doc["defaultACLAccess"]["subscribe"], False)


def may_publish(role, topic):
    """First matching publishClientSend ACL, highest priority first."""
    acls = sorted((a for a in roles[role]
                   if a["acltype"] == "publishClientSend"),
                  key=lambda a: -a["priority"])
    for acl in acls:
        if topic_matches(acl["topic"].replace("%u", "robot"), topic):
            return acl["allow"]
    return doc["defaultACLAccess"]["publishClientSend"]


command = "system_agent/nivuus/reboot/set"
check("only HA, the agent, the operator and legacy accounts may publish "
      "the agent's commands",
      sorted(r for r in roles if may_publish(r, command)),
      ["home-assistant", "legacy-full-access", "operator", "system-agent"])
check("zigbee2mqtt may not overwrite the agent's discovery",
      may_publish("zigbee2mqtt", "homeassistant/switch/nivuus/x/config"),
      False)
check("zigbee2mqtt publishes device discovery",
      may_publish("zigbee2mqtt", "homeassistant/light/0x00124b/l/config"),
      True)
for role, acls in roles.items():
    for acl in acls:
        for level in acl["topic"].split("/"):
            if "%" in level:
                check(f"{role}: substitution fills a whole level",
                      level in ("%u", "%c"), True)

# --- fresh install --------------------------------------------------------
with tempfile.TemporaryDirectory() as root:
    proc = run(root, {"mqtt_password": "wizard-pw"})
    check("fresh install succeeds", proc.returncode, 0)
    data = pathlib.Path(root) / DEST_REL / "mosquitto"
    check("the template is laid down", (data / "mosquitto.conf").read_text(),
          TEMPLATE)
    path = data / "dynamic-security.json"
    check("dynamic-security.json is 0600",
          oct(path.stat().st_mode & 0o777), "0o600")
    if os.geteuid() == 0:
        check("dynamic-security.json belongs to the broker uid",
              path.stat().st_uid, ma.MOSQUITTO_UID)
    found = clients(data)
    admin = env_admin(root)
    check("the admin secret lands in .env", bool(admin), True)
    check("the admin client verifies with it",
          verify(found[ma.ADMIN_USERNAME]["encoded_password"], admin), True)
    check("the wizard's mqtt_password becomes the shared account",
          verify(found["mqtt"]["encoded_password"], "wizard-pw"), True)
    check("no backup on a fresh install",
          (data / "mosquitto.conf.pre-dynsec").exists(), False)

    before = {p: p.read_bytes() for p in (path, data / "mosquitto.conf",
                                          pathlib.Path(root) / DEST_REL
                                          / ".env")}
    run(root, {"mqtt_password": "another"})
    check("a second pass rewrites nothing",
          {p: p.read_bytes() for p in before}, before)

# --- existing install on the 2026-08-28 configuration ---------------------
with tempfile.TemporaryDirectory() as root:
    data = install(root, conf=LEGACY, passwd=f"mqtt:{HASH}\n",
                   env="TZ=Europe/Paris\n")
    proc = run(root, {"mqtt_password": "ignored"})
    check("migration succeeds", proc.returncode, 0)
    found = clients(data)
    check("mqtt is carried with its hash, verbatim",
          found["mqtt"]["encoded_password"], HASH)
    check("mqtt keeps full access",
          found["mqtt"]["roles"], [{"rolename": "legacy-full-access"}])
    check("the wizard answer does not replace an existing account",
          sorted(found), sorted([ma.ADMIN_USERNAME, "mqtt"]))
    check("the configuration is migrated",
          (data / "mosquitto.conf").read_text(), TEMPLATE)
    check("the previous configuration is backed up, byte for byte",
          (data / "mosquitto.conf.pre-dynsec").read_text(), LEGACY)
    check("the password file is left as is",
          (data / "passwd").read_text(), f"mqtt:{HASH}\n")
    check("the existing .env keeps its values, gains the admin secret",
          ((pathlib.Path(root) / DEST_REL / ".env").read_text()
           .startswith("TZ=Europe/Paris\n"), bool(env_admin(root))),
          (True, True))

# --- a configuration its owner edited -------------------------------------
with tempfile.TemporaryDirectory() as root:
    edited = LEGACY + "max_connections 10\n"
    data = install(root, conf=edited, passwd=f"mqtt:{HASH}\n")
    proc = run(root)
    check("an edited configuration does not fail the phase",
          proc.returncode, 0)
    check("it is left untouched", (data / "mosquitto.conf").read_text(),
          edited)
    check("no accounts file for a configuration that will not load it",
          (data / "dynamic-security.json").exists(), False)
    check("and the hook says so on stderr",
          "edited by hand" in proc.stderr, True)
    check("no admin secret is drawn", env_admin(root), None)

# --- the broker already owns its accounts file ----------------------------
with tempfile.TemporaryDirectory() as root:
    owned = '{"clients": [{"username": "fully-salon"}], "roles": []}\n'
    data = install(root, conf=LEGACY, passwd=f"mqtt:{HASH}\n", dynsec=owned)
    proc = run(root)
    check("an existing accounts file does not fail the phase",
          proc.returncode, 0)
    check("an existing accounts file is never rewritten",
          (data / "dynamic-security.json").read_text(), owned)
    check("the configuration is still migrated",
          (data / "mosquitto.conf").read_text(), TEMPLATE)
    check("no admin secret is invented for a file the hook did not write",
          env_admin(root), None)

# --- an admin secret already in .env is reused ----------------------------
with tempfile.TemporaryDirectory() as root:
    data = install(root, conf=TEMPLATE,
                   env=f"{ma.ADMIN_ENV_KEY}=kept-secret\n")
    run(root)
    check("the .env secret is reused for a recreated accounts file",
          verify(clients(data)[ma.ADMIN_USERNAME]["encoded_password"],
                 "kept-secret"), True)
    check("and kept in .env", env_admin(root), "kept-secret")

# --- refused before the first write ---------------------------------------
with tempfile.TemporaryDirectory() as root:
    data = install(root, conf=LEGACY, passwd="mqtt:plaintext\n")
    proc = run(root)
    check("a plaintext password file stops the phase", proc.returncode, 1)
    check("the configuration is not touched",
          (data / "mosquitto.conf").read_text(), LEGACY)
    check("nothing else is laid down",
          (pathlib.Path(root) / DEST_REL / "docker-compose.yml").exists(),
          False)

with tempfile.TemporaryDirectory() as root:
    data = install(root, conf=LEGACY,
                   passwd=f"{ma.ADMIN_USERNAME}:{HASH}\n")
    check("an account already named like the admin stops the phase",
          run(root).returncode, 1)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_mqtt_accounts: OK")
