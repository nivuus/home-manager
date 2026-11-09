#!/usr/bin/env python3
"""The accounts migration writes, as root, into a directory the broker's uid
(1883) controls, and turns on a configuration that locks out every client its
accounts file does not know. Two guarantees, each tested here:

1. ORDER. A Dynamic Security configuration is never on disk without an
   accounts file carrying every password-file account, and that accounts
   file is never on disk without its admin secret saved in .env -- whatever
   step fails.

2. NO FOLLOWING. A symbolic link planted in the broker's directory makes the
   hook refuse, or is replaced, never written through.

Run: python3 tests/test_mqtt_migration_safety.py
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
LEGACY = "\n".join(ma.LEGACY_CONFS["password-file"]) + "\n"
HASH = ("$7$101$" + base64.b64encode(b"s" * 12).decode()
        + "$" + base64.b64encode(b"h" * 64).decode())
OUTSIDE_TEXT = "root:$6$c2FsdA==$aGFzaA==:19000:0:99999:7:::\n"

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
    _, _, iterations, salt, digest = encoded.split("$")
    return hashlib.pbkdf2_hmac(
        "sha512", password.encode(), base64.b64decode(salt),
        int(iterations), 64) == base64.b64decode(digest)


def env_admin(dest):
    env = dest / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        if line.startswith(ma.ADMIN_ENV_KEY + "="):
            return line.partition("=")[2]
    return None


def layout(root, conf=LEGACY, passwd=f"mqtt:{HASH}\n"):
    dest = pathlib.Path(root) / DEST_REL
    data = dest / "mosquitto"
    data.mkdir(parents=True)
    if conf is not None:
        (data / "mosquitto.conf").write_text(conf)
    if passwd is not None:
        (data / "passwd").write_text(passwd)
    return dest, data


def blocked_tmp(data):
    """A non-empty directory where the accounts file's temporary goes: the
    write of the accounts file fails, everything before it succeeded."""
    tmp = data / "dynamic-security.json.tmp"
    tmp.mkdir()
    (tmp / "keep").write_text("x")
    return tmp


# --- 1. order: the accounts file fails to be written ----------------------
with tempfile.TemporaryDirectory() as root:
    dest, data = layout(root)
    tmp = blocked_tmp(data)
    proc = run(root)
    check("legacy: a failed accounts write fails the phase",
          proc.returncode != 0, True)
    check("legacy: the configuration is still the password-file one",
          (data / "mosquitto.conf").read_text(), LEGACY)
    check("legacy: no accounts file", (data / "dynamic-security.json")
          .exists(), False)
    saved = env_admin(dest)
    check("legacy: the admin secret was saved BEFORE the accounts file",
          bool(saved), True)
    check("legacy: .env is 0600 from its creation",
          oct((dest / ".env").stat().st_mode & 0o777)
          if (dest / ".env").exists() else None, "0o600")

    (tmp / "keep").unlink()
    tmp.rmdir()
    check("legacy: the re-run succeeds", run(root).returncode, 0)
    accounts = json.loads((data / "dynamic-security.json").read_text())
    admin = next(c for c in accounts["clients"]
                 if c["username"] == ma.ADMIN_USERNAME)
    check("legacy: the re-run reuses the saved admin secret",
          verify(admin["encoded_password"], saved or ""), True)
    check("legacy: and keeps it in .env", env_admin(dest), saved)
    check("legacy: the configuration is migrated on the re-run",
          (data / "mosquitto.conf").read_text(), TEMPLATE)

with tempfile.TemporaryDirectory() as root:
    dest, data = layout(root, conf=None, passwd=None)
    blocked_tmp(data)
    proc = run(root)
    check("fresh: a failed accounts write fails the phase",
          proc.returncode != 0, True)
    check("fresh: the Dynamic Security configuration is NOT laid down",
          (data / "mosquitto.conf").exists(), False)

# --- 2. no following: planted symbolic links ------------------------------
# Each case: (name planted in the broker's directory, configuration, whether
# the hook must refuse). The link points at a file outside, which must keep
# its content, and nothing may be created next to it.
CASES = (
    ("mosquitto.conf", None, True),
    ("passwd", LEGACY, True),
    ("dynamic-security.json", LEGACY, True),
    ("mosquitto.conf.pre-dynsec", LEGACY, True),
    # A stale temporary is the hook's own leftover: it is removed (the link,
    # not its target) and the write goes on.
    ("dynamic-security.json.tmp", LEGACY, False),
)
for name, conf, refuses in CASES:
    with tempfile.TemporaryDirectory() as root, \
            tempfile.TemporaryDirectory() as outside:
        target = pathlib.Path(outside) / "victim"
        target.write_text(OUTSIDE_TEXT)
        dest, data = layout(root, conf=conf,
                            passwd=None if name == "passwd"
                            else f"mqtt:{HASH}\n")
        (data / name).symlink_to(target)
        proc = run(root)
        check(f"link {name}: refused" if refuses else f"link {name}: "
              "write goes on", proc.returncode != 0, refuses)
        check(f"link {name}: the target outside is untouched",
              target.read_text(), OUTSIDE_TEXT)
        check(f"link {name}: nothing is created outside",
              sorted(p.name for p in pathlib.Path(outside).iterdir()),
              ["victim"])
        if refuses:
            check(f"link {name}: nothing is laid down",
                  (dest / "docker-compose.yml").exists(), False)
            accounts = data / "dynamic-security.json"
            if name != "dynamic-security.json":
                check(f"link {name}: no accounts file",
                      accounts.exists(), False)
        else:
            check(f"link {name}: the accounts file is a regular file",
                  (data / "dynamic-security.json").is_symlink(), False)

with tempfile.TemporaryDirectory() as root, \
        tempfile.TemporaryDirectory() as outside:
    dest = pathlib.Path(root) / DEST_REL
    dest.mkdir(parents=True)
    (dest / "mosquitto").symlink_to(outside)
    proc = run(root)
    check("a linked broker directory is refused", proc.returncode != 0, True)
    check("nothing is written through the linked directory",
          sorted(os.listdir(outside)), [])

# --- progress messages added by the migration are in English --------------
with tempfile.TemporaryDirectory() as root:
    proc = run(root)
    msgs = [json.loads(line).get("msg", "") for line in
            proc.stdout.splitlines() if line.startswith("{")]
    check("the accounts step announces itself in English",
          "MQTT accounts" in msgs, True)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_mqtt_migration_safety: OK")
