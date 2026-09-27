#!/usr/bin/env python3
"""Install phase of the home-manager package: lay the stack down on the target.

The stack/ subtree IS the deployment directory, byte for byte, so laying it
down is a recursive copy -- there is no file list to keep up to date, and so
no file forgotten when a service is added. Two exceptions, removed AFTER the
copy: the environment template (it has done its job) and the development
overlay.

FOUR RULES CARRY THE REST.

1. NOTHING THAT CARRIES DATA IS OVERWRITTEN. config/ holds the automations,
   the database, the secrets and the tokens of a whole house; mosquitto/passwd
   the broker accounts; zigbee2mqtt/configuration.yaml the network_key of the
   Zigbee network; .env the MQTT password. This phase also runs on an
   already installed machine (`install.py --root /`), where a rewrite would
   silently destroy all of that. Keys missing from .env are ADDED, present
   ones are left alone -- EXCEPT ONE, COMPOSE_FILE, and only to remove a
   reference that rule 4 has just made invalid (see scrub_dev_overlay()): the
   only rewrite of an existing value this hook allows itself, because leaving
   it as is breaks the stack more surely than it protects it.

2. ANSWERS ARE VALIDATED BEFORE THE FIRST WRITE. The hook reads its context
   on stdin, and the standalone path the contract exists to allow -- a
   hand-written config.json -- goes through no validator. An unknown radio
   mode must fail the phase before the copy, not halfway through it.

3. WHAT BEARS THE PACKAGE'S NAME IS REPLACED, NOT MERGED.
   config/custom_components/personas_home/ is CODE laid down under a
   directory that rule 1 protects because it carries data. The copy merges:
   a module removed between two versions, or a stale __pycache__, would
   survive the update and Home Assistant would load it. See OWNED_TREES --
   the list names only what this package laid down, never the neighbouring
   integrations.

4. THE DEVELOPMENT FILE DOES NOT SHIP. docker-compose.dev.yml mounts
   repositories that exist only on its author's machine; elsewhere docker
   would create empty directories that Home Assistant would load as broken
   integrations. It is therefore removed from the deployment directory on
   EVERY pass, reinstalls included -- which can leave a pre-existing .env
   referencing a file that just disappeared; rule 1 says what the hook does
   about it.
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STACK = os.path.join(HERE, "stack")

DEST_REL = "opt/nivuus/home-manager"
TEMPLATE_NAME = "env.template"
DEV_OVERLAY = "docker-compose.dev.yml"

RADIO_MODES = ("reseau", "usb")

# Files that carry data: never overwritten if they already exist. Paths are
# relative to the deployment directory.
#
# zigbee2mqtt/configuration.yaml is the most dangerous of the three: it holds
# the network_key and pan_id of the network. Overwriting it does not "break"
# zigbee2mqtt, it forms ANOTHER network -- and every paired device stays on the
# old one, silent, without a single error message.
PRESERVED = (
    "config/configuration.yaml",
    "mosquitto/mosquitto.conf",
    "zigbee2mqtt/configuration.yaml",
)

# Files rendered from the template after the copy, with the same @KEYS@ as
# the .env. Those in PRESERVED are rendered only when just created:
# zigbee2mqtt reads its port from ITS file, not from the environment, so the
# wizard's answer must be written there on the first pass -- and never on a
# later one.
RENDERED = ("zigbee2mqtt/configuration.yaml",)

# Directories that bear this package's name: DELETED then copied again.
#
# They live under config/, which rule 1 protects -- but rule 1 protects DATA,
# and these carry CODE. A plain merge would keep a module removed between two
# versions, or a stale __pycache__, alive there: ghost files Home Assistant
# would load without a word.
#
# The list names ONLY what this package laid down. The neighbours in
# custom_components/ belong to the satellite packages and to the machine's
# owner; taking them along would uninstall the pantry while updating the
# base.
OWNED_TREES = ("config/custom_components/personas_home",)


def emit(event):
    print(json.dumps(event), flush=True)


def text_answer(answers, key, default=""):
    value = answers.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"la reponse {key!r} attend une chaine, recu {value!r}")
    return value.strip() or default


def parse_env(text):
    """The keys defined in a .env, in reading order."""
    keys = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            keys.append(stripped.partition("=")[0].strip())
    return keys


def merge_env(existing, rendered):
    """The existing .env, extended with only the keys it does not have yet.

    Nothing existing is touched: not the values, not the comments, not the
    order. New keys are appended at the end under a header saying where they
    come from.
    """
    have = set(parse_env(existing))
    added = [line for line in rendered.splitlines()
             if "=" in line and not line.strip().startswith("#")
             and line.partition("=")[0].strip() not in have]
    if not added:
        return existing
    tail = "\n# --- Ajoute par le package home-manager ---\n" + "\n".join(added)
    return existing.rstrip("\n") + "\n" + tail + "\n"


def scrub_dev_overlay(text):
    """Remove DEV_OVERLAY from COMPOSE_FILE in an EXISTING .env.

    Rule 4: DEV_OVERLAY is deleted from the deployment directory on every
    pass, reinstalls included. But an already present .env (so NEVER
    rewritten -- rule 1) may have been rendered by an earlier version of the
    hook, when the overlay was still deployed, and its COMPOSE_FILE still
    names it. A reference to a missing file in COMPOSE_FILE makes EVERY
    `docker compose` command impossible on the stack -- not only the
    development ones, ALL of them, `restart homeassistant` included. It is
    the only value of an existing .env this hook may rewrite, and only that
    one: never a missing key, never a value that does not name a file this
    hook has just removed.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "COMPOSE_FILE":
            continue
        parts = [p for p in value.split(":") if p and p != DEV_OVERLAY]
        lines[i] = f"{key}={':'.join(parts)}"
    body = "\n".join(lines)
    return body + "\n" if text.endswith("\n") else body


def render_env(values):
    with open(os.path.join(STACK, TEMPLATE_NAME)) as fh:
        text = fh.read()
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    return text


def copy_stack(dest):
    """Copy stack/ to dest without overwriting PRESERVED.

    Returns the set of PRESERVED relative paths this copy CREATED -- those
    that already existed are not in it. The caller uses it to render the
    templates on the first pass only.
    """
    existing = {rel for rel in PRESERVED
                if os.path.isfile(os.path.join(dest, rel))}
    saved = {}
    for rel in existing:
        with open(os.path.join(dest, rel), "rb") as fh:
            saved[rel] = fh.read()

    # What this package owns is removed BEFORE the copy, which lays it down
    # whole again. copytree has no "replace": without this pass it merges,
    # and the files of an earlier version stay.
    #
    # Absence is the only normal case. Any other error (a symbolic link, a
    # denied permission) must stop the phase: swallowing it would let
    # copytree write through the link, outside what this package owns.
    for rel in OWNED_TREES:
        path = os.path.join(dest, rel)
        if os.path.lexists(path):
            shutil.rmtree(path)

    shutil.copytree(STACK, dest, symlinks=True, dirs_exist_ok=True)

    # Restore what the copy just overwrote. Save then restore, rather than
    # filtering the copy: copytree has no "do not overwrite", and an ignore=
    # on the names would also skip the file on a fresh install, where it must
    # be laid down.
    for rel, content in saved.items():
        with open(os.path.join(dest, rel), "wb") as fh:
            fh.write(content)

    for name in (TEMPLATE_NAME, DEV_OVERLAY):
        path = os.path.join(dest, name)
        if os.path.exists(path):
            os.remove(path)

    return set(PRESERVED) - existing


def render_file(path, values):
    """Replace the @KEYS@ of a deployed file, in place."""
    with open(path) as fh:
        text = fh.read()
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    with open(path, "w") as fh:
        fh.write(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()

    ctx = json.load(sys.stdin)
    answers = ctx.get("answers") or {}
    root = args.root.rstrip("/") or "/"

    # Rule 2: validate before laying down the first byte.
    try:
        radio_mode = text_answer(answers, "radio_mode", "reseau")
        if radio_mode not in RADIO_MODES:
            raise ValueError(
                f"radio_mode attend l'un de {RADIO_MODES}, recu {radio_mode!r}")
        values = {
            "TZ": text_answer(answers, "timezone", "Europe/Paris"),
            "SLZB_HOST": text_answer(answers, "slzb_host", "192.168.0.79"),
            "THREAD_PORT": text_answer(answers, "thread_port", "6638"),
            "ZIGBEE_PORT": text_answer(answers, "zigbee_port", "7638"),
            "RCP_BAUDRATE": text_answer(answers, "rcp_baudrate", "460800"),
            "THREAD_DEVICE": text_answer(answers, "thread_device",
                                         "/dev/ttyACM0"),
            "ZIGBEE_DEVICE": text_answer(answers, "zigbee_device",
                                         "/dev/ttyACM1"),
            "THREAD_TXPOWER": text_answer(answers, "thread_txpower", "20"),
            "BACKBONE_IF": text_answer(answers, "backbone_if", "localBridge"),
        }
    except ValueError as exc:
        print(f"home-manager install: {exc}", file=sys.stderr)
        return 1

    # The USB overlay is merged only in USB mode: it follows from the radio
    # mode, it is not one more question.
    compose_files = "docker-compose.yml"
    if radio_mode == "usb":
        compose_files += ":docker-compose.usb.yml"
    values["COMPOSE_FILE"] = compose_files

    dest = os.path.join(root, DEST_REL)

    emit({"event": "progress", "pct": 25, "msg": "Depose de la pile"})
    created = copy_stack(dest)

    # Deployed templates are rendered on the first pass only: a file already
    # present carries the real configuration, and injecting the wizard's
    # answers again would replace the live Zigbee network with a new one.
    for rel in RENDERED:
        if rel in created:
            render_file(os.path.join(dest, rel), values)

    emit({"event": "progress", "pct": 65, "msg": "Rendu de la configuration"})
    rendered = render_env(values)
    env_path = os.path.join(dest, ".env")
    if os.path.isfile(env_path):
        with open(env_path) as fh:
            existing = fh.read()
        rendered = merge_env(existing, rendered)
        # DEV_OVERLAY has just been removed from the deployment directory
        # (rule 4), even on this reinstall. If the COMPOSE_FILE this .env
        # already carried still named it, leaving it as is would break every
        # `docker compose` command on the stack -- see scrub_dev_overlay().
        rendered = scrub_dev_overlay(rendered)
        emit({"event": "progress", "pct": 75,
              "msg": ".env existant conserve, variables manquantes ajoutees"})
    with open(env_path, "w") as fh:
        fh.write(rendered)
    # 0600: the .env carries the broker password.
    os.chmod(env_path, 0o600)

    emit({"event": "progress", "pct": 90,
          "msg": f"Domotique deposee dans /{DEST_REL}"})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
