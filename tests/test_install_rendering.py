#!/usr/bin/env python3
"""A rendered template is either absent or finished, whatever interrupts the
install phase.

zigbee2mqtt/configuration.yaml is created once and preserved afterwards (it
holds the Zigbee network's keys). If the template could be seen in the
deployment directory before its placeholders were replaced, an interruption
there would leave `port: tcp://@SLZB_HOST@:@ZIGBEE_PORT@` in a file every
later run preserves: zigbee2mqtt never starts, and nothing points back here.

Run: python3 tests/test_install_rendering.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hooks"))
import mqtt_accounts  # noqa: E402

HOOK = REPO / "hooks" / "install.py"
DEST_REL = "opt/nivuus/home-manager"
Z2M_REL = "zigbee2mqtt/configuration.yaml"
ANSWERS = {"slzb_host": "10.0.0.5", "zigbee_port": "7638"}

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def run(root, answers=ANSWERS):
    return subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root",
         str(root)], input=json.dumps({"answers": answers}),
        capture_output=True, text=True)


# --- interrupted between the copy and the rendering -----------------------
# The broker migration sits between the two and can fail: here its accounts
# file is already there, so the copy runs, and the configuration to migrate
# is read-only, so the in-place rewrite is refused.
with tempfile.TemporaryDirectory() as root:
    dest = pathlib.Path(root) / DEST_REL
    data = dest / "mosquitto"
    data.mkdir(parents=True)
    conf = data / "mosquitto.conf"
    conf.write_text(
        "\n".join(mqtt_accounts.LEGACY_CONFS["password-file"]) + "\n")
    (data / "dynamic-security.json").write_text(json.dumps(
        {"clients": [{"username": mqtt_accounts.ADMIN_USERNAME}],
         "roles": []}))
    conf.chmod(0o444)

    proc = run(root)
    if os.geteuid() == 0:
        print("test_install_rendering: running as root, the read-only "
              "configuration cannot interrupt the phase; case skipped")
    else:
        check("a refused migration fails the phase", proc.returncode, 1)
        check("the stack was copied before the failure",
              (dest / "docker-compose.yml").is_file(), True)
        check("no template is left in place by the interrupted run",
              (dest / Z2M_REL).exists(), False)

        conf.chmod(0o644)
        check("the re-run succeeds", run(root).returncode, 0)
        check("the re-run renders the file from the answers",
              yaml.safe_load((dest / Z2M_REL).read_text())["serial"]["port"],
              "tcp://10.0.0.5:7638")

# --- interrupted while the rendered file is being written ------------------
# The temporary of the rendered file cannot be created (a non-empty
# directory holds its name): the file must be absent, not half written.
with tempfile.TemporaryDirectory() as root:
    dest = pathlib.Path(root) / DEST_REL
    blocker = dest / "zigbee2mqtt" / "configuration.yaml.tmp"
    blocker.mkdir(parents=True)
    (blocker / "keep").write_text("x")

    proc = run(root)
    check("a rendering that cannot be written fails the phase",
          proc.returncode, 1)
    check("nothing is in place of the file", (dest / Z2M_REL).exists(),
          False)

    (blocker / "keep").unlink()
    blocker.rmdir()
    check("the re-run after a failed write succeeds", run(root).returncode,
          0)
    text = (dest / Z2M_REL).read_text()
    check("it renders the file from the answers",
          yaml.safe_load(text)["serial"]["port"], "tcp://10.0.0.5:7638")
    check("no placeholder of the hook survives",
          [m for m in ("@SLZB_HOST@", "@ZIGBEE_PORT@") if m in text], [])
    check("no temporary is left behind",
          sorted(q.name for q in (dest / "zigbee2mqtt").iterdir()
                 if q.name.endswith(".tmp")), [])

# --- every placeholder of the template is one the hook renders ------------
# Rendered with no answer at all (the hook's defaults): a placeholder the
# hook has no value for would survive, and zigbee2mqtt would read it.
with tempfile.TemporaryDirectory() as root:
    check("install with default answers succeeds",
          run(root, {}).returncode, 0)
    text = (pathlib.Path(root) / DEST_REL / Z2M_REL).read_text()
    template = (REPO / "stack" / Z2M_REL).read_text()
    placeholders = sorted(set(re.findall(r"@[A-Z][A-Z0-9_]*@", template)))
    check("the template has placeholders to render", bool(placeholders),
          True)
    check("none of them survives the rendering",
          [m for m in placeholders if m in text], [])

# --- a template an earlier, interrupted install left behind ---------------
with tempfile.TemporaryDirectory() as root:
    dest = pathlib.Path(root) / DEST_REL
    (dest / "zigbee2mqtt").mkdir(parents=True)
    stale = (REPO / "stack" / Z2M_REL).read_text()
    (dest / Z2M_REL).write_text(stale)

    proc = run(root)
    check("an unrendered template in place stops the phase",
          proc.returncode, 1)
    check("the message names the file", Z2M_REL in proc.stderr, True)
    check("the message names the placeholders",
          ("@SLZB_HOST@" in proc.stderr, "@ZIGBEE_PORT@" in proc.stderr),
          (True, True))
    check("the file is reported, not rewritten",
          (dest / Z2M_REL).read_text(), stale)
    check("nothing is laid down before the refusal",
          (dest / "docker-compose.yml").exists(), False)

    # The remedy the message gives.
    (dest / Z2M_REL).unlink()
    check("deleting it lets the next run through", run(root).returncode, 0)
    check("and that run renders it",
          yaml.safe_load((dest / Z2M_REL).read_text())["serial"]["port"],
          "tcp://10.0.0.5:7638")

# --- a live configuration is not mistaken for a template ------------------
# '@' is ordinary in YAML values; only this hook's own placeholders count.
with tempfile.TemporaryDirectory() as root:
    dest = pathlib.Path(root) / DEST_REL
    (dest / "zigbee2mqtt").mkdir(parents=True)
    live = ("advanced:\n  network_key: [1, 2, 3]\n"
            "devices:\n  '0x1':\n    friendly_name: user@HOME@lan\n")
    (dest / Z2M_REL).write_text(live)
    check("a live configuration with '@' in a value passes",
          run(root).returncode, 0)
    check("and is left byte for byte", (dest / Z2M_REL).read_text(), live)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_install_rendering: OK")
