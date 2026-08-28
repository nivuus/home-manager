#!/usr/bin/env python3
"""Le wizard doit couvrir tout ce que le compose interpole, et rien de plus.

Une question sans variable est une promesse non tenue : l'operateur repond, et
rien ne lit sa reponse. Une variable sans question est un litteral deguise :
le package redevient installable chez une seule personne.

Run: python3 tests/test_wizard_answers.py
"""
import pathlib
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
QUESTIONS = yaml.safe_load((REPO / "wizard.yaml").read_text())

# Les six types du moteur, verbatim de packages/wizard.py. La liste y est
# CLOSE : un type invente est refuse au chargement, pas ignore.
TYPES = {"bool", "choix", "texte", "secret", "disque", "gpu"}

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


keys = [q["key"] for q in QUESTIONS]
check("aucune cle en double", len(keys), len(set(keys)))

for question in QUESTIONS:
    check(f"{question['key']}: type connu",
          question["type"] in TYPES, True)
    check(f"{question['key']}: libelle non vide",
          bool(question.get("label", "").strip()), True)

# Les valeurs de l'hote de reference sont les defauts : une installation chez
# Maxime doit pouvoir se faire en validant le formulaire tel quel.
defaults = {q["key"]: q.get("default") for q in QUESTIONS}
check("defaut: mode radio", defaults["radio_mode"], "reseau")
check("defaut: hote de la passerelle", defaults["slzb_host"], "192.168.0.79")
check("defaut: port Thread", defaults["thread_port"], "6638")
check("defaut: port Zigbee", defaults["zigbee_port"], "7638")
check("defaut: baud Thread", defaults["rcp_baudrate"], "460800")
check("defaut: txpower Thread", defaults["thread_txpower"], "20")
check("defaut: interface backbone", defaults["backbone_if"], "localBridge")
check("defaut: fuseau", defaults["timezone"], "Europe/Paris")

# Le mot de passe du broker est un secret : le portail ne doit pas l'afficher
# en clair, et il ne doit pas avoir de defaut — un mot de passe par defaut
# partage par toutes les installations est pire que pas de mot de passe.
mqtt = next(q for q in QUESTIONS if q["key"] == "mqtt_password")
check("mot de passe MQTT en secret", mqtt["type"], "secret")
check("mot de passe MQTT sans defaut", mqtt.get("default"), None)

# La cle est « choices », en anglais, meme si le type s'appelle « choix » :
# c'est ce que lit packages/wizard.py, et un « choix: » y serait vu comme une
# question de type choix SANS choix, donc refusee.
check("mode radio propose les deux modes",
      sorted(next(q for q in QUESTIONS
                  if q["key"] == "radio_mode")["choices"]),
      ["reseau", "usb"])

# Chaque cle du wizard doit finir dans une variable du gabarit, et chaque
# variable du gabarit doit venir d'une cle du wizard.
template = (REPO / "stack" / "env.template").read_text()
placeholders = {line.split("=", 1)[1].strip().strip("@")
                for line in template.splitlines()
                if "=@" in line}
expected = {"TZ", "SLZB_HOST", "THREAD_PORT", "ZIGBEE_PORT", "RCP_BAUDRATE",
            "THREAD_DEVICE", "ZIGBEE_DEVICE", "THREAD_TXPOWER", "BACKBONE_IF",
            "COMPOSE_FILE"}
check("le gabarit porte exactement les variables attendues",
      placeholders, expected)

# COMPOSE_FILE est calcule par le hook, pas demande : c'est une consequence du
# mode radio, pas une question.
check("COMPOSE_FILE n'est pas une question",
      "compose_file" in keys, False)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_wizard_answers: OK")
