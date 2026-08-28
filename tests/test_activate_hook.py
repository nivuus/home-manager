#!/usr/bin/env python3
"""La phase activate demarre la pile sans marcher sur les pieds de personne.

Elle est testee par ses fonctions pures : lancer reellement docker demanderait
un demon, et un test qui demarre six conteneurs sur la machine de
developpement n'est pas un test.

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

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_activate_hook: OK")
