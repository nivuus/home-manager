#!/usr/bin/env python3
"""Le compose ne doit plus rien savoir de la machine sur laquelle il tourne.

Chaque assertion garde une regression precise et deja constatee :
- cinq montages pointaient sur /home/mallanic/Projects, absents de toute autre
  machine ;
- tous les chemins de donnees etaient ecrits en absolu sur
  /opt/nivuus/HomeAssistant, ce qui interdisait de deplacer le deploiement ;
- l'adresse du SLZB-MR2U, ses deux ports, le baud et le txpower Thread etaient
  des litteraux, ce qui rendait le package installable chez une seule personne.

Run: python3 tests/test_compose_portable.py
"""
import pathlib
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
STACK = REPO / "stack"
MAIN = STACK / "docker-compose.yml"
USB = STACK / "docker-compose.usb.yml"
DEV = STACK / "docker-compose.dev.yml"

SERVICES = ("homeassistant", "docker-socket-proxy", "mosquitto",
            "zigbee2mqtt", "otbr", "matterjs-server")

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


main = yaml.safe_load(MAIN.read_text())

# Le nom de projet est epingle : il ne derive plus du nom du repertoire, donc
# renommer le deploiement n'orpheline pas les conteneurs.
check("nom de projet", main.get("name"), "home-manager")

check("les six services sont declares", sorted(main["services"]),
      sorted(SERVICES))

# Aucun chemin de machine, dans aucun des deux fichiers deployes.
for label, path in (("main", MAIN), ("usb", USB)):
    text = path.read_text()
    check(f"{label}: pas de /home/", "/home/" in text, False)
    check(f"{label}: pas de l'ancien emplacement",
          "/opt/nivuus/HomeAssistant" in text, False)

# Les donnees sont montees en relatif : le deploiement est deplacable.
for service, expected in (("homeassistant", "./config:/config"),
                          ("otbr", "./otbr:/var/lib/thread"),
                          ("zigbee2mqtt", "./zigbee2mqtt:/app/data"),
                          ("matterjs-server", "./matter_js:/data")):
    check(f"{service}: donnees en relatif",
          expected in (main["services"][service].get("volumes") or []), True)

# L'adresse et les ports de la passerelle radio sont des variables.
otbr_env = main["services"]["otbr"]["environment"]
check("otbr: hote de la radio en variable",
      otbr_env["RCP_HOST"], "${SLZB_HOST}")
check("otbr: port Thread en variable",
      otbr_env["RCP_PORT"], "${THREAD_PORT}")
check("otbr: baud en variable",
      otbr_env["RCP_BAUDRATE"], "${RCP_BAUDRATE}")
check("otbr: interface backbone en variable",
      otbr_env["OTBR_BACKBONE_IF"], "${BACKBONE_IF}")

# Le txpower Thread est reapplique en boucle parce qu'otbr-agent ne le
# persiste pas et qu'un redemarrage interne le remettrait a 0 dBm. La valeur
# doit venir du wizard : +20 dBm est le maximum du SLZB-MR2U, pas une constante
# universelle.
check("otbr: txpower parametre",
      "${THREAD_TXPOWER}" in str(main["services"]["otbr"]["command"]), True)

# Le proxy Docker n'ecoute que sur la boucle locale. Une regression ici
# exposerait l'API Docker au reseau : c'est la raison d'etre du service.
check("proxy: ecoute limitee a la boucle locale",
      main["services"]["docker-socket-proxy"]["ports"],
      ["127.0.0.1:2375:2375"])
check("proxy: socket monte en lecture seule",
      "/var/run/docker.sock:/var/run/docker.sock:ro"
      in main["services"]["docker-socket-proxy"]["volumes"], True)
check("proxy: exec refuse",
      main["services"]["docker-socket-proxy"]["environment"]["EXEC"], 0)

# Home Assistant ne monte plus le socket Docker brut : il passe par le proxy.
ha_volumes = str(main["services"]["homeassistant"].get("volumes"))
check("HA ne monte pas le socket Docker",
      "docker.sock" in ha_volumes, False)

# Les binds de developpement vivent dans un fichier NON deploye : sans cela,
# une installation neuve monterait des repertoires inexistants.
dev = yaml.safe_load(DEV.read_text())
check("le fichier de dev cible Home Assistant",
      sorted(dev["services"]), ["homeassistant"])
check("le fichier de dev porte bien les binds locaux",
      "/home/mallanic" in DEV.read_text(), True)

# La surcouche USB remplace le mode reseau des radios.
usb = yaml.safe_load(USB.read_text())
check("la surcouche USB ne touche que les radios",
      sorted(usb["services"]), ["otbr", "zigbee2mqtt"])

# Toute variable interpolee doit etre declaree dans le gabarit, sinon docker
# compose substitue une chaine vide sans le dire.
template = (STACK / "env.template").read_text()
declared = {line.split("=", 1)[0].strip()
            for line in template.splitlines()
            if "=" in line and not line.strip().startswith("#")}
interpolated = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}",
                              MAIN.read_text() + USB.read_text()))
check("aucune variable interpolee absente du gabarit",
      sorted(interpolated - declared), [])

# Reciproquement, une variable du gabarit que personne ne lit promet quelque
# chose qu'elle ne tient pas.
#
# Deux exceptions, et chacune est lue par quelqu'un d'autre que docker :
# COMPOSE_FILE est lu par docker compose lui-meme ; ZIGBEE_PORT sert a
# hooks/install.py pour rendre zigbee2mqtt/configuration.yaml, parce que
# zigbee2mqtt lit son port dans SON fichier de donnees et non dans
# l'environnement.
NOT_INTERPOLATED = {"COMPOSE_FILE", "ZIGBEE_PORT"}
check("aucune variable du gabarit orpheline",
      sorted(declared - interpolated - NOT_INTERPOLATED), [])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_compose_portable: OK")
