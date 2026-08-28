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


def compose(*args):
    """Docker Compose dans le repertoire de deploiement."""
    return subprocess.run(["docker", "compose", *args], cwd=DEPLOY,
                          capture_output=True, text=True)


def compose_services(*args):
    """La liste de services rendue par une sous-commande compose. [] si echec."""
    proc = compose(*args)
    if proc.returncode != 0:
        return []
    return [name for name in (proc.stdout or "").split() if name]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--root", default="/")
    args = parser.parse_args()
    json.load(sys.stdin)          # le contexte est lu, rien n'en depend ici

    emit({"event": "progress", "pct": 10, "msg": "Demarrage de la domotique"})
    todo = startup_order(services_to_start(
        compose_services("config", "--services"),
        compose_services("ps", "-a", "--services")))

    if not todo:
        emit({"event": "progress", "pct": 90,
              "msg": "Tous les services ont deja un conteneur, rien a creer"})
        emit({"event": "done"})
        return 0

    proc = compose("up", "-d", *todo)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        emit({"event": "progress", "pct": 60,
              "msg": "Demarrage partiel : "
                     + (tail[-1][:160] if tail else "sans detail")})
        # Fatal seulement si RIEN ne tourne (regle 3).
        if not compose_services("ps", "--services"):
            print("home-manager activate: aucun service n'a demarre",
                  file=sys.stderr)
            return 1

    emit({"event": "progress", "pct": 95,
          "msg": "Home Assistant repond sur le port 8123"})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
