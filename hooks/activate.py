#!/usr/bin/env python3
"""Phase activate du package home-manager : demarrer la pile.

Ici et pas en phase install, parce qu'il faut le reseau : six images doivent
etre tirees.

TROIS REGLES.

1. SEULS LES SERVICES SANS CONTENEUR SONT DEMARRES. Un `docker compose up -d`
   global redemarre aussi les conteneurs ARRETES — or un conteneur arrete l'a
   souvent ete a dessein. Un service qui a deja un conteneur a un
   proprietaire, et ce n'est pas cette phase.

2. LE BROKER DEMARRE EN PREMIER. zigbee2mqtt publie des son demarrage et Home
   Assistant ouvre sa connexion MQTT a l'initialisation de l'integration ;
   demarres avant le broker, les deux passent par une fenetre de reconnexion
   inutile. C'est precisement la dependance circulaire que ce package supprime
   en reprenant mosquitto au docker_marketplace de Home Assistant, et la
   respecter ici est la moitie de l'interet.

   Un depends_on dans le compose aurait ete le reflexe. Il ne convient pas :
   les cinq autres services sont en network_mode: host et n'ont aucune raison
   d'attendre les uns les autres, et un depends_on sur un service que
   l'operateur a arrete bloquerait le demarrage de toute la pile.

3. UN ECHEC PARTIEL N'EST PAS FATAL. Une passerelle radio injoignable empeche
   otbr de demarrer, et ce n'est pas une raison de declarer la domotique en
   echec : Home Assistant tourne, le reste se repare a chaud. Seul un
   demarrage ou RIEN ne tourne l'est.

A FAILED COMPOSE LISTING IS AN ERROR, never an answer. `config --services`
failing used to read as "nothing declared" and report the phase done with
nothing started; `ps -a --services` failing used to read as "no container
exists" and start every service -- the global start rule 1 forbids. Both now
raise ActivationError with Compose's last error line, and the phase exits
non-zero (the same shape as media-manager's activate hook).

THE LAST MESSAGE REPORTS WHAT WAS MEASURED: the services `ps --services` lists
as running. It makes no claim about a port this phase never contacted.
"""
import argparse
import json
import subprocess
import sys

DEPLOY = "/opt/nivuus/home-manager"

# Le broker, avant tout ce qui publie dessus (regle 2).
FIRST = "mosquitto"


def emit(event):
    print(json.dumps(event), flush=True)


def services_to_start(declared, existing):
    """Les services declares qui n'ont encore AUCUN conteneur.

    L'ordre de `declared` est conserve : une liste stable rend la trace de
    progression lisible.
    """
    have = set(existing)
    return [name for name in declared if name not in have]


def startup_order(services):
    """`services` avec le broker en tete, le reste dans l'ordre recu.

    Rend une liste meme quand le broker est absent — il peut avoir deja un
    conteneur, et cette phase ne le touche alors pas.
    """
    return ([FIRST] if FIRST in services else []) + \
           [name for name in services if name != FIRST]


class ActivationError(RuntimeError):
    """A docker compose call failed: the phase cannot trust its view of the stack."""


def last_line(text):
    """The last non-empty line of Compose's output, the one that names the error."""
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1][:160] if lines else "no detail"


def compose(*args):
    """Docker Compose dans le repertoire de deploiement."""
    try:
        return subprocess.run(["docker", "compose", *args], cwd=DEPLOY,
                              capture_output=True, text=True)
    except OSError as error:
        raise ActivationError(f"docker compose {' '.join(args)}: {error}") \
            from error


def compose_services(*args):
    """The service names one compose subcommand prints.

    Raises ActivationError on a non-zero exit instead of returning []: an
    empty list is a real answer ("nothing declared", "no container", "nothing
    runs") and a failure must never be mistaken for one.
    """
    proc = compose(*args)
    if proc.returncode != 0:
        raise ActivationError(
            f"docker compose {' '.join(args)}: {last_line(proc.stderr)}")
    return [name for name in (proc.stdout or "").split() if name]


def running_message(running):
    """The final progress line: the services measured to run, nothing more."""
    return "Running services: " + (", ".join(running) if running else "none")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    json.load(sys.stdin)          # le contexte est lu, rien n'en depend ici

    try:
        return run_phase()
    except ActivationError as exc:
        print(f"home-manager activate: {exc}", file=sys.stderr)
        return 1


def run_phase():
    """Start the services that have no container, then report what runs."""
    emit({"event": "progress", "pct": 10, "msg": "Demarrage de la domotique"})
    todo = startup_order(services_to_start(
        compose_services("config", "--services"),
        compose_services("ps", "-a", "--services")))

    if not todo:
        emit({"event": "progress", "pct": 90,
              "msg": "Tous les services ont deja un conteneur, rien a creer"})
    else:
        proc = compose("up", "-d", *todo)
        if proc.returncode != 0:
            emit({"event": "progress", "pct": 60,
                  "msg": "Partial start: " + last_line(proc.stderr)})

    running = compose_services("ps", "--services")
    # Fatal only when a start left NOTHING running (rule 3). With nothing to
    # create, an empty list is the operator's choice, not this phase's failure.
    if todo and not running:
        print("home-manager activate: no service is running", file=sys.stderr)
        return 1
    emit({"event": "progress", "pct": 95, "msg": running_message(running)})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
