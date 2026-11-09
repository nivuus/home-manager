#!/usr/bin/env python3
"""Real-broker proof of the per-client MQTT accounts migration.

What the unit tests cannot show -- that Mosquitto itself accepts the files the
install hook writes, and enforces the roles the way the design says -- is
proven here against the broker image the compose file runs.

Each broker is a THROWAWAY container with NO network (`--network none`), and
every client runs inside it with `docker exec`. Nothing is published on the
host, no docker network is created, and the containers are removed at exit.

Scenario: a legacy install (password file, the 2026-08-28 configuration, a
sha512-pbkdf2 hash in Mosquitto 2.0's format) -> the legacy broker accepts
`mqtt` -> the install hook migrates -> the new broker accepts `mqtt` on its
carried-over hash -> per-device clients are created with mosquitto_ctrl ->
every role is checked where it must work and where it must not -> a second
hook pass changes nothing -> rollback to the backed-up configuration works.

Run as root (the data directory is handed to uid 1883), with docker:
    DYNSEC_TEST_DIR=/root/work make test-broker
"""
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hooks"))
import mqtt_accounts  # noqa: E402

import yaml  # noqa: E402

# The image the package runs, read from stack/docker-compose.yml: the proof is
# only worth something against the broker that will load these files.
IMAGE = yaml.safe_load((REPO / "stack" / "docker-compose.yml").read_text())[
    "services"]["mosquitto"]["image"]
NAME = f"dynsec-test-{os.getpid()}"
DEST_REL = "opt/nivuus/home-manager"
CERT = "/mosquitto/data/certs/server.crt"

LEGACY_PW = secrets.token_urlsafe(12)
MEROSS_ACL = "pattern readwrite /appliance/#\npattern readwrite /app/#\n"

failures = []


def check(label, got, want):
    status = "ok  " if got == want else "FAIL"
    print(f"  [{status}] {label}")
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def sh(*args, check_rc=True, **kw):
    proc = subprocess.run(list(args), capture_output=True, text=True, **kw)
    if check_rc and proc.returncode != 0:
        raise RuntimeError(f"{args}: {proc.stderr.strip()}")
    return proc


def legacy_hash(password):
    """sha512-pbkdf2 as Mosquitto 2.0's mosquitto_passwd wrote it: 101
    iterations, 12-byte salt -- the format of the live password file."""
    return mqtt_accounts.encode_pbkdf2(password, 101, secrets.token_bytes(12))


def start_broker(data):
    sh("docker", "run", "--rm", "-d", "--network", "none", "--name", NAME,
       "-v", f"{data}:/mosquitto/data",
       "-v", f"{data}/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro",
       IMAGE)
    for _ in range(50):
        probe = sh("docker", "exec", NAME, "sh", "-c",
                   "grep -q 'running' /mosquitto/data/mosquitto.log "
                   "&& netstat -ltn | grep -q ':1883 '", check_rc=False)
        if probe.returncode == 0:
            return
        time.sleep(0.2)
    logs = sh("docker", "logs", NAME, check_rc=False)
    raise RuntimeError("broker did not start:\n" + logs.stdout + logs.stderr)


def stop_broker():
    sh("docker", "rm", "-f", NAME, check_rc=False)


def client_args(port, user=None, password=None):
    args = ["-h", "localhost", "-p", str(port), "-V", "5"]
    if port == 8883:
        args += ["--cafile", CERT, "--insecure"]
    if user is not None:
        args += ["-u", user]
    if password is not None:
        args += ["-P", password]
    return args


def pub(topic, payload, port=1883, user=None, password=None, retain=False):
    args = ["docker", "exec", NAME, "mosquitto_pub", *client_args(
        port, user, password), "-q", "1", "-t", topic, "-m", payload]
    if retain:
        args.append("-r")
    return sh(*args, check_rc=False)


def receives(filter_, port=1883, user=None, password=None):
    """Retained payloads a subscription to filter_ gets within 1.5 s."""
    proc = sh("docker", "exec", NAME, "mosquitto_sub", *client_args(
        port, user, password), "-t", filter_, "-W", "1.5", check_rc=False)
    return proc.stdout.split()


class Spy:
    """A full-access subscriber on '#' that tells whether a publish landed."""

    def __init__(self, user, password):
        sh("docker", "exec", "-d", NAME, "sh", "-c",
           f"mosquitto_sub -h localhost -p 1883 -u '{user}' -P '{password}' "
           "-t '#' -v > /tmp/spy.log 2>&1")
        time.sleep(0.5)

    def landed(self, token):
        time.sleep(0.3)
        log = sh("docker", "exec", NAME, "cat", "/tmp/spy.log").stdout
        return token in log


def can_publish(spy, topic, port=1883, user=None, password=None):
    token = secrets.token_hex(6)
    pub(topic, token, port, user, password)
    return spy.landed(token)


def can_subscribe(owner, filter_, probe_topic, port=1883, user=None,
                  password=None):
    """Whether a client subscribing to filter_ receives a retained message
    published on probe_topic by `owner` (a full-access account)."""
    token = secrets.token_hex(6)
    pub(probe_topic, token, 1883, *owner, retain=True)
    got = token in receives(filter_, port, user, password)
    pub(probe_topic, "", 1883, *owner, retain=True)   # clear the retained msg
    return got


def ctrl(admin_pw, *args):
    return sh("docker", "exec", NAME, "mosquitto_ctrl", "-u",
              mqtt_accounts.ADMIN_USERNAME, "-P", admin_pw, "dynsec", *args)


def run_hook(root, answers=None):
    ctx = json.dumps({"answers": answers or {}})
    return sh(sys.executable, str(REPO / "hooks" / "install.py"),
              "--phase", "install", "--root", str(root), input=ctx,
              check_rc=False)


def make_legacy_install(root):
    data = root / DEST_REL / "mosquitto"
    (data / "certs").mkdir(parents=True)
    (data / "mosquitto.conf").write_text(
        "# Mosquitto Configuration (legacy, hand-commented)\n"
        + "\n".join(mqtt_accounts.LEGACY_CONFS["password-file"]) + "\n")
    (data / "passwd").write_text(f"mqtt:{legacy_hash(LEGACY_PW)}\n")
    (data / "acl_meross").write_text(MEROSS_ACL)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days",
       "2", "-subj", "/CN=localhost", "-keyout", str(data / "certs/server.key"),
       "-out", str(data / "certs/server.crt"))
    return data


def main():
    if os.geteuid() != 0:
        print("broker_dynsec_container: must run as root (chown to 1883)")
        return 2
    base = os.environ.get("DYNSEC_TEST_DIR")
    root = pathlib.Path(tempfile.mkdtemp(prefix="dynsec-", dir=base))
    legacy = ("mqtt", LEGACY_PW)
    try:
        data = make_legacy_install(root)
        sh("chown", "-R", "1883:1883", str(data))

        print("== legacy broker (password file)")
        start_broker(data)
        spy = Spy(*legacy)
        check("legacy: mqtt publishes on 1883 with a 2.0-format hash",
              can_publish(spy, "probe/legacy", 1883, *legacy), True)
        stop_broker()

        print("== install hook migrates")
        proc = run_hook(root)
        check("hook exits 0", proc.returncode, 0)
        dynsec = data / "dynamic-security.json"
        check("dynamic-security.json owned by 1883, mode 0600",
              (dynsec.stat().st_uid, oct(dynsec.stat().st_mode & 0o777)),
              (1883, "0o600"))
        check("configuration now the packaged template",
              (data / "mosquitto.conf").read_text(),
              (REPO / "stack/mosquitto/mosquitto.conf").read_text())
        env = (root / DEST_REL / ".env").read_text()
        admin_pw = [line.split("=", 1)[1] for line in env.splitlines()
                    if line.startswith(mqtt_accounts.ADMIN_ENV_KEY + "=")][0]

        print("== migrated broker (Dynamic Security)")
        start_broker(data)
        spy = Spy(*legacy)
        for port in (1883, 1884, 8884):
            check(f"legacy mqtt logs in and publishes on {port}",
                  can_publish(spy, f"probe/legacy{port}", port, *legacy),
                  True)
            check(f"anonymous refused on {port}",
                  pub("probe/anon", "x", port).returncode != 0, True)
            check(f"wrong password refused on {port}",
                  pub("probe/bad", "x", port, "mqtt", "wrong").returncode
                  != 0, True)

        started = time.monotonic()
        ctrl(admin_pw, "listClients")
        print(f"  admin login + listClients: "
              f"{time.monotonic() - started:.2f} s "
              f"({mqtt_accounts.ADMIN_ITERATIONS} PBKDF2 iterations)")

        users = {
            "homeassistant": "home-assistant",
            "zigbee2mqtt": "zigbee2mqtt",
            "mqtt-system-agent": "system-agent",
            "GenerousHideousBison": "valetudo",
            "WanImpartialQuail": "valetudo",
            "fully-salon": "fully-kiosk",
            "operator": "operator",
        }
        pw = {}
        for user, role in users.items():
            pw[user] = secrets.token_urlsafe(12)
            ctrl(admin_pw, "createClient", user, "-p", pw[user])
            ctrl(admin_pw, "addClientRole", user, role)
        c = {user: (user, pw[user]) for user in users}

        cmd = "system_agent/nivuus/reboot/set"
        print("== who may publish the system agent's command topics")
        for user in ("homeassistant", "mqtt-system-agent"):
            check(f"{user} may publish {cmd}",
                  can_publish(spy, cmd, 1883, *c[user]), True)
        for user in ("zigbee2mqtt", "GenerousHideousBison", "fully-salon"):
            check(f"{user} may NOT publish {cmd}",
                  can_publish(spy, cmd, 1883, *c[user]), False)
            check(f"{user} may NOT subscribe system_agent/#",
                  can_subscribe(legacy, "system_agent/#",
                                "system_agent/nivuus/state", 1883, *c[user]),
                  False)

        print("== zigbee2mqtt")
        z = c["zigbee2mqtt"]
        check("publishes zigbee2mqtt/<device>",
              can_publish(spy, "zigbee2mqtt/lamp", 1883, *z), True)
        check("publishes discovery homeassistant/light/0x00124b/light/config",
              can_publish(spy, "homeassistant/light/0x00124b/light/config",
                          1883, *z), True)
        check("may NOT overwrite the agent's discovery",
              can_publish(spy, "homeassistant/switch/nivuus/x/config", 1883,
                          *z), False)
        check("may NOT publish homeassistant/status",
              can_publish(spy, "homeassistant/status", 1883, *z), False)
        check("receives zigbee2mqtt/<device>/set",
              can_subscribe(legacy, "zigbee2mqtt/#", "zigbee2mqtt/lamp/set",
                            1883, *z), True)
        check("receives homeassistant/status",
              can_subscribe(legacy, "homeassistant/status",
                            "homeassistant/status", 1883, *z), True)
        check("may NOT publish valetudo/...",
              can_publish(spy, "valetudo/GenerousHideousBison/x/set", 1883,
                          *z), False)

        print("== system agent")
        a = c["mqtt-system-agent"]
        check("publishes system_agent/nivuus/cpu/state",
              can_publish(spy, "system_agent/nivuus/cpu/state", 1883, *a),
              True)
        check("publishes its discovery homeassistant/sensor/nivuus/cpu/config",
              can_publish(spy, "homeassistant/sensor/nivuus/cpu/config", 1883,
                          *a), True)
        check("receives system_agent/nivuus/+/set",
              can_subscribe(legacy, "system_agent/nivuus/+/set", cmd, 1883,
                            *a), True)
        check("receives homeassistant/status",
              can_subscribe(legacy, "homeassistant/status",
                            "homeassistant/status", 1883, *a), True)
        check("may NOT publish other discovery",
              can_publish(spy, "homeassistant/light/0x00124b/light/config",
                          1883, *a), False)
        check("may NOT publish zigbee2mqtt/...",
              can_publish(spy, "zigbee2mqtt/lamp/set", 1883, *a), False)

        print("== Valetudo (%u = robot identifier)")
        r = c["GenerousHideousBison"]
        check("publishes valetudo/<own id>/...",
              can_publish(spy, "valetudo/GenerousHideousBison/state", 1883,
                          *r), True)
        check("publishes its own discovery",
              can_publish(spy, "homeassistant/vacuum/GenerousHideousBison/"
                          "GenerousHideousBison_vacuum/config", 1883, *r),
              True)
        check("receives valetudo/<own id>/.../set",
              can_subscribe(legacy, "valetudo/GenerousHideousBison/#",
                            "valetudo/GenerousHideousBison/fan/set", 1883,
                            *r), True)
        check("may NOT publish the other robot's tree",
              can_publish(spy, "valetudo/WanImpartialQuail/fan/set", 1883,
                          *r), False)
        check("may NOT subscribe the other robot's tree",
              can_subscribe(legacy, "valetudo/WanImpartialQuail/#",
                            "valetudo/WanImpartialQuail/fan/set", 1883, *r),
              False)
        check("may NOT subscribe valetudo/+/# (wildcard over the id level)",
              can_subscribe(legacy, "valetudo/+/#",
                            "valetudo/WanImpartialQuail/fan/set", 1883, *r),
              False)
        check("may NOT publish the other robot's discovery",
              can_publish(spy, "homeassistant/vacuum/WanImpartialQuail/x/"
                          "config", 1883, *r), False)

        print("== Fully Kiosk")
        f = c["fully-salon"]
        check("publishes fully/salon/event/screenOn",
              can_publish(spy, "fully/salon/event/screenOn", 1883, *f), True)
        check("publishes fully/deviceInfo/<deviceId>",
              can_publish(spy, "fully/deviceInfo/abc123", 1883, *f), True)
        check("may NOT subscribe anything (fully/#)",
              can_subscribe(legacy, "fully/#", "fully/salon/event/x", 1883,
                            *f), False)
        check("may NOT publish homeassistant/...",
              can_publish(spy, "homeassistant/status", 1883, *f), False)

        print("== Home Assistant and operator")
        check("HA publishes anywhere (zigbee2mqtt/lamp/set)",
              can_publish(spy, "zigbee2mqtt/lamp/set", 1883,
                          *c["homeassistant"]), True)
        check("HA subscribes #",
              can_subscribe(legacy, "#", "valetudo/WanImpartialQuail/x",
                            1883, *c["homeassistant"]), True)
        check("operator subscribes # on 1884",
              can_subscribe(legacy, "#", "zigbee2mqtt/lamp", 1884,
                            *c["operator"]), True)
        check("admin may NOT publish application topics",
              can_publish(spy, cmd, 1883, mqtt_accounts.ADMIN_USERNAME,
                          admin_pw), False)

        print("== Meross listener 8883 (anonymous, acl_meross, TLS)")
        mac = ("48:e1:e9:00:00:01", "bulb-key")
        for label, who in (("MAC username", mac), ("no username", ())):
            check(f"{label}: publishes /appliance/<uuid>/publish",
                  can_publish(spy, "/appliance/u1/publish", 8883, *who), True)
            check(f"{label}: publishes /app/<id>/subscribe",
                  can_publish(spy, "/app/42/subscribe", 8883, *who), True)
            check(f"{label}: may NOT publish {cmd}",
                  can_publish(spy, cmd, 8883, *who), False)
            check(f"{label}: may NOT publish zigbee2mqtt/lamp/set",
                  can_publish(spy, "zigbee2mqtt/lamp/set", 8883, *who), False)
            check(f"{label}: receives /appliance/<uuid>/subscribe",
                  can_subscribe(legacy, "/appliance/#",
                                "/appliance/u1/subscribe", 8883, *who), True)
            check(f"{label}: '#' does NOT leak zigbee2mqtt/lamp",
                  can_subscribe(legacy, "#", "zigbee2mqtt/lamp", 8883, *who),
                  False)

        print("== a second hook pass changes nothing")
        stop_broker()
        before = dynsec.read_bytes()
        conf_before = (data / "mosquitto.conf").read_text()
        proc = run_hook(root)
        check("second pass exits 0", proc.returncode, 0)
        check("dynamic-security.json untouched (operator clients kept)",
              dynsec.read_bytes() == before, True)
        check("configuration untouched",
              (data / "mosquitto.conf").read_text(), conf_before)
        start_broker(data)
        spy = Spy(*legacy)
        check("per-device client survives restart + second pass",
              can_publish(spy, "zigbee2mqtt/after", 1883, *z), True)

        print("== final step: delete the shared account")
        ctrl(admin_pw, "deleteClient", "mqtt")
        check("mqtt refused once deleted",
              pub("probe/x", "x", 1883, *legacy).returncode != 0, True)
        stop_broker()

        print("== rollback to the backed-up configuration")
        shutil.copyfile(data / "mosquitto.conf.pre-dynsec",
                        data / "mosquitto.conf")
        start_broker(data)
        spy = Spy(*legacy)
        check("rollback: mqtt works again from the untouched passwd",
              can_publish(spy, "probe/rollback", 1883, *legacy), True)
    finally:
        stop_broker()
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print("\n".join(["", "FAILURES:"] + failures))
        return 1
    print("broker_dynsec_container: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
