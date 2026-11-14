#!/usr/bin/env python3
"""La phase activate demarre la pile sans marcher sur les pieds de personne.

Elle est testee par ses fonctions pures : lancer reellement docker demanderait
un demon, et un test qui demarre six conteneurs sur la machine de
developpement n'est pas un test.

main() itself is driven too, with `activate.compose` replaced by a fake that
answers from a table: the failure paths of each Compose listing are checked
without ever running docker.

Run: python3 tests/test_activate_hook.py
"""
import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "activate", REPO / "hooks" / "activate.py")
activate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activate)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# Un service qui a deja un conteneur — meme arrete — a un proprietaire, et ce
# n'est pas cette phase. Un `up -d` global ressusciterait un conteneur que
# l'operateur a arrete a dessein.
check("seuls les services sans conteneur sont demarres",
      activate.services_to_start(
          ["homeassistant", "otbr", "mosquitto"],
          ["homeassistant"]),
      ["otbr", "mosquitto"])

check("aucun service a creer quand tout existe",
      activate.services_to_start(["homeassistant"], ["homeassistant"]), [])

check("l'ordre declare est conserve",
      activate.services_to_start(["a", "b", "c"], []), ["a", "b", "c"])

# Le broker doit demarrer AVANT ses consommateurs : zigbee2mqtt publie des le
# demarrage, et Home Assistant ouvre sa connexion MQTT a l'initialisation de
# l'integration. C'est toute la raison d'avoir repris mosquitto au marketplace.
order = activate.startup_order(
    ["homeassistant", "zigbee2mqtt", "mosquitto", "otbr"])
check("le broker demarre en premier", order[0], "mosquitto")
check("le broker precede zigbee2mqtt",
      order.index("mosquitto") < order.index("zigbee2mqtt"), True)
check("le broker precede Home Assistant",
      order.index("mosquitto") < order.index("homeassistant"), True)
check("aucun service n'est perdu en route", sorted(order),
      sorted(["homeassistant", "zigbee2mqtt", "mosquitto", "otbr"]))

check("un ordre sans broker reste inchange",
      activate.startup_order(["otbr", "homeassistant"]),
      ["otbr", "homeassistant"])


# --- main() against a stubbed compose ---------------------------------------
# No test runs the real docker: `activate.compose` is replaced by a fake that
# answers each subcommand from a table and records every call.
import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import types  # noqa: E402


def run_main(answers):
    """Run main() with `answers` {args tuple: (returncode, stdout, stderr)}.

    Returns (exit status, calls made, emitted events, stderr text).
    """
    calls = []

    def fake_compose(*args):
        calls.append(args)
        code, out, err = answers.get(args, (0, "", ""))
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)

    real = activate.compose
    activate.compose = fake_compose
    argv, stdin = sys.argv, sys.stdin
    sys.argv = ["activate.py", "--phase", "activate"]
    sys.stdin = io.StringIO("{}")
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = activate.main()
    finally:
        activate.compose = real
        sys.argv, sys.stdin = argv, stdin
    events = [json.loads(line) for line in out.getvalue().splitlines() if line]
    return status, calls, events, err.getvalue()


def ups(calls):
    return [c for c in calls if c[:1] == ("up",)]


CONFIG = ("config", "--services")
PS_ALL = ("ps", "-a", "--services")
PS_RUNNING = ("ps", "--services")
DECLARED = "mosquitto\nhomeassistant\notbr\n"

# A failing listing of the declared services is an error, not "nothing to do".
status, calls, events, err = run_main({
    CONFIG: (1, "", "level=warning\nerror: invalid compose file\n")})
check("failed config listing: non-zero exit", status != 0, True)
check("failed config listing: nothing started", ups(calls), [])
check("failed config listing: not reported done",
      any(e.get("event") == "done" for e in events), False)
check("failed config listing: Compose's last error line is reported",
      "error: invalid compose file" in err, True)

# A failing listing of the existing containers must never read as "no
# container exists": that would start every service, including ones stopped on
# purpose.
status, calls, events, err = run_main({
    CONFIG: (0, DECLARED, ""),
    PS_ALL: (1, "", "Cannot connect to the Docker daemon\n")})
check("failed ps -a listing: non-zero exit", status != 0, True)
check("failed ps -a listing: nothing started", ups(calls), [])
check("failed ps -a listing: Compose's last error line is reported",
      "Cannot connect to the Docker daemon" in err, True)

# A normal start: only the missing services, broker first, and the final
# message names what was measured to run -- no claim about a port nobody
# contacted.
status, calls, events, err = run_main({
    CONFIG: (0, DECLARED, ""),
    PS_ALL: (0, "homeassistant\n", ""),
    PS_RUNNING: (0, "homeassistant\nmosquitto\notbr\n", "")})
check("normal start: exit 0", status, 0)
check("normal start: only the missing services, broker first",
      ups(calls), [("up", "-d", "mosquitto", "otbr")])
messages = " ".join(e.get("msg", "") for e in events)
check("normal start: no unverified port claim", "8123" in messages, False)
check("normal start: the running services are reported",
      all(name in messages for name in ("homeassistant", "mosquitto", "otbr")),
      True)

# Nothing to create: still a success, and still no port claim.
status, calls, events, err = run_main({
    CONFIG: (0, DECLARED, ""),
    PS_ALL: (0, DECLARED, ""),
    PS_RUNNING: (0, "homeassistant\n", "")})
check("nothing to create: exit 0", status, 0)
check("nothing to create: nothing started", ups(calls), [])
check("nothing to create: no unverified port claim",
      "8123" in " ".join(e.get("msg", "") for e in events), False)

# Every container stopped on purpose and nothing to create: not this phase's
# failure, and the report says that nothing runs.
status, calls, events, err = run_main({
    CONFIG: (0, DECLARED, ""),
    PS_ALL: (0, DECLARED, ""),
    PS_RUNNING: (0, "", "")})
check("all stopped on purpose: exit 0", status, 0)
check("all stopped on purpose: nothing started", ups(calls), [])

# Rule 3, kept: a partial start is tolerated while something runs ...
status, calls, events, err = run_main({
    CONFIG: (0, DECLARED, ""),
    PS_ALL: (0, "", ""),
    ("up", "-d", "mosquitto", "homeassistant", "otbr"):
        (1, "", "otbr: radio unreachable\n"),
    PS_RUNNING: (0, "mosquitto\nhomeassistant\n", "")})
check("partial start: tolerated", status, 0)
check("partial start: the failure line is reported",
      "otbr: radio unreachable" in " ".join(e.get("msg", "") for e in events),
      True)

# ... fatal when nothing runs ...
status, calls, events, err = run_main({
    CONFIG: (0, DECLARED, ""),
    PS_ALL: (0, "", ""),
    ("up", "-d", "mosquitto", "homeassistant", "otbr"): (1, "", "boom\n"),
    PS_RUNNING: (0, "", "")})
check("nothing runs after up: fatal", status != 0, True)

# ... and an unreadable running list after a failed up is an error too, never
# taken for "something runs".
status, calls, events, err = run_main({
    CONFIG: (0, DECLARED, ""),
    PS_ALL: (0, "", ""),
    ("up", "-d", "mosquitto", "homeassistant", "otbr"): (1, "", "boom\n"),
    PS_RUNNING: (1, "", "daemon gone\n")})
check("unreadable running list: fatal", status != 0, True)
check("unreadable running list: reported", "daemon gone" in err, True)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_activate_hook: OK")
