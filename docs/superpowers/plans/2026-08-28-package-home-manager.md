# Package Nivuus `home-manager` — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire de la domotique de `/opt/nivuus/HomeAssistant` un package Nivuus installable, portable et testable, socle des futurs satellites (tablettes, stocks).

**Architecture:** Un dépôt calqué sur `media-manager` : `nivuus-package.yaml` + `wizard.yaml` décrivent le package au moteur, `stack/` est le répertoire de déploiement à l'octet près (copié vers `/opt/nivuus/home-manager`), `hooks/install.py` dépose et rend la configuration sans jamais écraser une donnée, `hooks/activate.py` démarre la pile. Les six services vivent dans **un seul compose**, là où ils sont aujourd'hui répartis entre trois fichiers, un marketplace et un `docker run` oublié.

**Tech Stack:** Docker Compose v2, Python 3.11 (stdlib + PyYAML), tests en scripts autonomes lancés par `make test`.

**Spec:** `docs/superpowers/specs/2026-08-28-package-nivuus-home-manager-design.md`

## Global Constraints

- Répertoire de déploiement : **`/opt/nivuus/home-manager`** (constante `DEST_REL = "opt/nivuus/home-manager"`).
- **Aucun chemin absolu de machine** dans le compose déployé : ni `/home/mallanic`, ni `/opt/nivuus/HomeAssistant`. Tout passe par des variables du `.env` ou des chemins relatifs.
- **Rien de ce qui porte de la donnée n'est jamais écrasé** : `config/`, `secrets.yaml`, `.env`, `mosquitto/passwd`, `mosquitto/certs/`. La règle du `.env` de `media-manager` s'applique ici à la configuration d'une maison entière.
- Le package **ne versionne aucune** configuration Home Assistant personnelle. Le seul YAML livré est un `configuration.yaml` de démarrage, créé uniquement s'il est absent.
- Seuls les fichiers **suivis par git** voyagent (`git archive HEAD` dans `iso-build/build.sh`). Un fichier ignoré n'existe pas pour l'installer.
- `tier: userspace` : ni `kernel-cmdline`, ni `modules`, ni `hugepages-mib`, ni `claims`.
- Le watchdog OTBR **n'entre pas** dans le package (désactivé le 2026-05-04 : 213 reboots SLZB/semaine, 0 récupération).
- Les hooks parlent jsonl sur stdout, lisent leur contexte JSON sur stdin, acceptent `--phase` et `--root`.
- Messages destinés à l'opérateur en français.
- Répertoire de travail : `~/Projects/Nivuus/packages/home-manager`.

---

### Task 1 : dépôt, manifeste et test du contrat

Le manifeste est la seule chose que le moteur lit avant d'accepter d'exécuter du code du package. Il vient donc en premier, avec le test qui le valide par le **vrai** parseur du moteur.

**Files:**
- Create: `nivuus-package.yaml`, `.gitignore`, `Makefile`, `tests/test_manifest_contract.py`

**Interfaces:**
- Consumes: rien (première tâche).
- Produces: un package nommé `home-manager`, `tier: userspace`, découvrable par `discover()`. Les tâches suivantes ajoutent `wizard.yaml`, `hooks/` et `stack/` aux clés déjà déclarées ici.

- [ ] **Step 1: Initialiser le dépôt**

```bash
cd ~/Projects/Nivuus/packages/home-manager
git init
git config user.email "maxime@allanic.me"
```

- [ ] **Step 2: Écrire le `.gitignore`**

Créer `.gitignore` :

```gitignore
__pycache__/
*.pyc
.pytest_cache/
```

Rien d'autre : tout fichier ignoré ici serait absent de l'ISO, `build.sh` n'exportant que ce que git suit.

- [ ] **Step 3: Écrire le test du contrat**

Créer `tests/test_manifest_contract.py` :

```python
#!/usr/bin/env python3
"""Le manifeste doit passer le parseur du moteur, pas une relecture locale.

NIVUUS_INSTALLER_DIR fait valider par installer/packages/manifest.py, qui fait
autorite. Sans lui, la reverification locale ci-dessous garde le depot testable
seul — mais elle ne remplace pas le vrai parseur.

Run: python3 tests/test_manifest_contract.py
     make test NIVUUS_INSTALLER_DIR=$HOME/Projects/Nivuus/packages/installer
"""
import os
import pathlib
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = REPO / "nivuus-package.yaml"

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


data = yaml.safe_load(MANIFEST.read_text())

check("apiVersion", data.get("apiVersion"), "nivuus.dev/v1")
check("nom", data.get("name"), "home-manager")
check("tier", data.get("tier"), "userspace")

# tier userspace : le parseur REFUSE le fichier si ces cles sont declarees, il
# ne les ignore pas. La domotique ne touche pas la chaine de demarrage.
check("aucun bloc platform", "platform" in data, False)

# Pas de claims : un claim exclusif sur les radios les rendrait mutuellement
# exclusives avec tout futur satellite qui les partagerait.
check("aucun claim", "claims" in data, False)

# Un package declare ses PROPRES dependances plutot que de se coupler a la
# liste de features du moteur — precedent pose par console avec firewalld.
# requires.features: [docker] casserait l'installation autonome sur une Debian
# ordinaire, que le contrat existe precisement pour permettre.
check("docker declare en apt", "docker.io" in (data.get("apt") or []), True)
check("compose declare en apt",
      "docker-compose-v2" in (data.get("apt") or []), True)
check("pas de requires.features",
      "features" in (data.get("requires") or {}), False)

check("wizard declare", (data.get("wizard") or {}).get("questions"),
      "wizard.yaml")
check("hook install", (data.get("hooks") or {}).get("install"),
      "hooks/install.py")
check("hook activate", (data.get("hooks") or {}).get("activate"),
      "hooks/activate.py")

# Pas de hook resolve : rien a resoudre avant l'ecriture, et refuser une
# machine serait faux — Home Assistant tourne partout.
check("pas de hook resolve", "resolve" in (data.get("hooks") or {}), False)

# Le parseur qui fait autorite, quand il est disponible.
installer = os.environ.get("NIVUUS_INSTALLER_DIR")
if installer:
    sys.path.insert(0, str(pathlib.Path(installer) / "installer"))
    from packages.manifest import load_manifest

    manifest = load_manifest(str(MANIFEST))
    check("parseur du moteur: nom", manifest.name, "home-manager")
    check("parseur du moteur: tier", manifest.tier, "userspace")
    check("parseur du moteur: aucune dependance", manifest.packages, ())
    check("parseur du moteur: hook install resolu",
          manifest.hook_path("install").endswith("hooks/install.py"), True)
else:
    print("NIVUUS_INSTALLER_DIR absent : verification locale seule")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_manifest_contract: OK")
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il échoue**

Run: `python3 tests/test_manifest_contract.py`
Expected: FAIL — `FileNotFoundError: nivuus-package.yaml`

- [ ] **Step 5: Écrire le manifeste**

Créer `nivuus-package.yaml` :

```yaml
apiVersion: nivuus.dev/v1
name: home-manager
version: 1.0.0
label: "Domotique (Home Assistant, Thread, Zigbee, Matter, MQTT)"
tier: userspace

# Aucun `requires:`, aucun `claims:`, aucun `platform:` — les trois
# deliberement.
#
# `requires.features: [docker]` aurait ete le reflexe. Le precedent pose par
# console avec firewalld tranche : un package declare ses PROPRES dependances
# plutot que de se coupler a la liste de features du moteur, sinon
# l'installation autonome sur une Debian ordinaire — que le contrat existe
# precisement pour permettre — casse. Si l'operateur a coche la feature docker,
# l'apt ci-dessous est un no-op idempotent.
#
# `claims:` aurait ete faux. Les radios Thread et Zigbee sont jointes par le
# reseau (SLZB-MR2U en ser2net), pas par un peripherique que deux packages se
# disputeraient. Un claim exclusif ferait entrer ce package en conflit avec
# tout futur satellite partageant la meme passerelle — l'inverse de ce que le
# modele socle + satellites demande.
#
# `requires.capabilities` : rien dans le vocabulaire du moteur (iommu,
# gpu-discrete, nvme-dedicated, cpu-hybrid) n'est exige par la domotique. Les
# demander masquerait le package sur des machines ou il fonctionne.

apt:
  - docker.io
  - docker-compose-v2

wizard:
  questions: wizard.yaml

hooks:
  install: hooks/install.py
  activate: hooks/activate.py
```

- [ ] **Step 6: Écrire le Makefile**

Créer `Makefile` :

```makefile
# Package Nivuus home-manager — cible de test.
#
# Les suites sont des scripts Python autonomes, pas du pytest : c'est le style
# du depot installer, et il ne demande rien d'autre que python3 + PyYAML.
#
# NIVUUS_INSTALLER_DIR fait valider le manifeste par le VRAI parseur du moteur
# (installer/packages/manifest.py) au lieu de la reverification locale.
#   make test NIVUUS_INSTALLER_DIR=$$HOME/Projects/Nivuus/packages/installer

PACKAGE_DIR := $(CURDIR)
PYTHON ?= python3

.PHONY: test help

help:
	@grep -E '^[a-zA-Z_-]+:.*' $(MAKEFILE_LIST) | sed 's/:.*//' | sort

test:
	@for t in test_manifest_contract test_compose_portable \
	          test_install_hook test_activate_hook test_wizard_answers; do \
	    echo "--- $$t"; \
	    $(PYTHON) $(PACKAGE_DIR)/tests/$$t.py || exit 1; \
	done
```

Les quatre suites encore absentes arrivent aux tâches 2 à 5 ; `make test` échoue tant qu'elles manquent, ce qui est le comportement voulu.

- [ ] **Step 7: Lancer le test pour vérifier qu'il passe**

Run: `python3 tests/test_manifest_contract.py`
Expected: PASS — `test_manifest_contract: OK`

- [ ] **Step 8: Vérifier avec le parseur du moteur**

Run: `NIVUUS_INSTALLER_DIR=$HOME/Projects/Nivuus/packages/installer python3 tests/test_manifest_contract.py`
Expected: PASS, sans la ligne `NIVUUS_INSTALLER_DIR absent`.

- [ ] **Step 9: Commit**

```bash
git add .gitignore Makefile nivuus-package.yaml tests/test_manifest_contract.py
git commit -m "feat: manifeste du package nivuus home-manager"
```

---

### Task 2 : le compose unifié et son gabarit

Les six services en un seul fichier, sans un seul chemin de machine. C'est le cœur du package.

**Files:**
- Create: `stack/docker-compose.yml`, `stack/docker-compose.usb.yml`, `stack/docker-compose.dev.yml`, `stack/env.template`, `tests/test_compose_portable.py`

**Interfaces:**
- Consumes: rien du code des autres tâches.
- Produces: les variables du `.env` que `hooks/install.py` (Task 4) doit rendre — `TZ`, `SLZB_HOST`, `THREAD_PORT`, `ZIGBEE_PORT`, `RCP_BAUDRATE`, `THREAD_TXPOWER`, `BACKBONE_IF`, `COMPOSE_FILE` — et les six noms de services que `hooks/activate.py` (Task 5) démarre : `homeassistant`, `docker-socket-proxy`, `mosquitto`, `zigbee2mqtt`, `otbr`, `matterjs-server`.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_compose_portable.py` :

```python
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
# Trois exceptions, et chacune est lue par quelqu'un d'autre que docker :
# COMPOSE_FILE est lu par docker compose lui-meme ; ZIGBEE_PORT et SLZB_HOST
# servent a hooks/install.py pour rendre zigbee2mqtt/configuration.yaml, parce
# que zigbee2mqtt lit son port dans SON fichier de donnees et non dans
# l'environnement. SLZB_HOST est de toute facon interpole par otbr, donc seul
# ZIGBEE_PORT a besoin d'etre excuse ici.
NOT_INTERPOLATED = {"COMPOSE_FILE", "ZIGBEE_PORT"}
check("aucune variable du gabarit orpheline",
      sorted(declared - interpolated - NOT_INTERPOLATED), [])

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_compose_portable: OK")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 tests/test_compose_portable.py`
Expected: FAIL — `FileNotFoundError: stack/docker-compose.yml`

- [ ] **Step 3: Écrire le compose principal**

Créer `stack/docker-compose.yml` :

```yaml
# Package Nivuus home-manager — les six services de la domotique.
#
# Ils vivaient dans trois fichiers, un marketplace et un `docker run` oublie :
# docker-compose.yml (HA, otbr, zigbee2mqtt), docker-socket-proxy/ (le proxy),
# config/stacks/mosquitto/ genere par l'integration docker_marketplace de Home
# Assistant, et matterjs-server que RIEN ne decrivait.
#
# Reunir mosquitto ici supprime une dependance circulaire reelle : Home
# Assistant lancait le broker dont Home Assistant lui-meme depend, et
# zigbee2mqtt avec lui. Si HA ne demarrait pas, la domotique ne remontait pas.
name: home-manager

services:
  homeassistant:
    container_name: homeassistant
    image: ghcr.io/home-assistant/home-assistant:stable
    labels:
      com.centurylinklabs.watchtower.enable: true
    volumes:
      - ./config:/config
      - ./media:/media
      - /etc/localtime:/etc/localtime:ro
      - /run/dbus:/run/dbus:ro
      # Le socket Docker brut a ete retire le 2026-07-27 : HA ne joint le
      # demon qu'a travers docker-socket-proxy, sur 127.0.0.1:2375. Monter
      # /var/run/docker.sock ici est un root-sur-l-hote trivial si HA ou une
      # integration tierce est compromise.
      - /dev:/dev
    restart: always
    privileged: true
    network_mode: host
    user: "0:0"

  # L'unique conteneur qui touche le socket brut. Il expose une API Docker
  # filtree sur la boucle locale, jamais sur le reseau.
  docker-socket-proxy:
    container_name: docker-socket-proxy
    image: tecnativa/docker-socket-proxy:latest
    restart: always
    read_only: true
    tmpfs:
      - /run
      - /tmp
    environment:
      PING: 1
      VERSION: 1
      INFO: 1
      EVENTS: 1          # monitor_docker suit les ajouts/retraits
      CONTAINERS: 1
      IMAGES: 1
      NETWORKS: 1
      VOLUMES: 1
      POST: 1            # le marketplace deploie et arrete des applications
      # Refus explicites, pour qu'un audit les voie.
      EXEC: 0
      BUILD: 0
      COMMIT: 0
      AUTH: 0
      SECRETS: 0
      CONFIGS: 0
      SWARM: 0
      SERVICES: 0
      TASKS: 0
      NODES: 0
      PLUGINS: 0
      SYSTEM: 0
      SESSION: 0
      DISTRIBUTION: 0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    ports:
      - "127.0.0.1:2375:2375"

  mosquitto:
    container_name: mosquitto
    image: eclipse-mosquitto:latest
    restart: always
    volumes:
      - ./mosquitto:/mosquitto/data
      - ./mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "1883:1883"
      - "1884:1884"
      - "8883:8883"
      - "8884:8884"

  # Bordure Thread. Radio 1 (EFR32MG21) du SLZB-MR2U, joignable en ser2net :
  # RCP_USE_TCP=1 fait ouvrir a socat un pont tcp -> pty qu'otbr-agent utilise.
  # Ce mode reseau remplace l'USB depuis le 2026-07-18 et supprime les
  # ttyACM changeants, usbguard et les « error -71 ».
  otbr:
    container_name: otbr
    image: bnutzer/otbr-tcp
    restart: always
    privileged: true
    network_mode: host
    devices:
      - /dev/net/tun
    environment:
      RCP_USE_TCP: "1"
      RCP_HOST: "${SLZB_HOST}"
      RCP_PORT: "${THREAD_PORT}"
      RCP_TTY: "/tmp/ttyOTBR"
      RCP_BAUDRATE: "${RCP_BAUDRATE}"
      # Le SLZB post-reset a le controle de flux materiel desactive sur l'UART
      # EFR32 : l'option par defaut de l'image (« &uart-flow-control ») bloque
      # alors le TX vers la radio — la reception fonctionne, les commandes ne
      # parviennent jamais.
      OTBR_RCP_ADDITIONAL_ARGS: ""
      OTBR_REST_LISTEN_ADDRESS: "0.0.0.0"
      OTBR_REST_LISTEN_PORT: "8082"
      OTBR_BACKBONE_IF: "${BACKBONE_IF}"
    volumes:
      - ./otbr:/var/lib/thread
    # otbr-agent NE PERSISTE PAS le txpower : s6-overlay execute ce command
    # apres le demarrage des services, et la boucle le reapplique toutes les
    # 5 minutes pour couvrir un redemarrage interne d'otbr-agent. Retirer ce
    # command, c'est revenir au defaut 0 dBm.
    command:
      - /bin/sh
      - -c
      - until ot-ctl txpower ${THREAD_TXPOWER}; do sleep 5; done;
        while true; do sleep 300;
        ot-ctl txpower ${THREAD_TXPOWER} >/dev/null 2>&1 || true; done

  # Zigbee. Radio 2 (CC2652P) du meme SLZB-MR2U, sur son propre port ser2net.
  # Remplace ZHA depuis le 2026-07-14 : les rideaux Tuya TS030F ignoraient
  # go_to_lift_percentage sous ZHA.
  zigbee2mqtt:
    container_name: zigbee2mqtt
    image: koenkk/zigbee2mqtt:latest
    restart: always
    network_mode: host
    volumes:
      - ./zigbee2mqtt:/app/data
      - /run/udev:/run/udev:ro
      - /etc/localtime:/etc/localtime:ro
    environment:
      TZ: "${TZ}"

  # Controleur Matter. Ce service n'existait dans AUCUN fichier : il ne vivait
  # que dans le demon Docker, sans label ni compose, donc irreproductible.
  matterjs-server:
    container_name: matterjs-server
    image: ghcr.io/matter-js/matterjs-server:stable
    restart: unless-stopped
    network_mode: host
    environment:
      LOG_LEVEL: info
      STORAGE_PATH: /data
    volumes:
      - ./matter_js:/data
    command: ["--storage-path", "/data", "--primary-interface", "${BACKBONE_IF}"]
```

- [ ] **Step 4: Écrire la surcouche USB**

Créer `stack/docker-compose.usb.yml` :

```yaml
# Surcouche « radios en USB ». Le mode par defaut est le reseau (ser2net sur
# une passerelle type SLZB-MR2U) ; cette surcouche s'active quand le wizard
# repond que les radios sont branchees en direct.
#
# Elle n'est pas un fichier de rechange : docker compose FUSIONNE, donc seules
# les cles presentes ici ecrasent celles du fichier principal.
services:
  otbr:
    environment:
      # Le mode TCP est neutralise ; otbr-agent ouvre directement le device.
      RCP_USE_TCP: "0"
      RCP_TTY: "${THREAD_DEVICE}"
    devices:
      - /dev/net/tun
      - "${THREAD_DEVICE}:${THREAD_DEVICE}"

  zigbee2mqtt:
    devices:
      - "${ZIGBEE_DEVICE}:${ZIGBEE_DEVICE}"
```

- [ ] **Step 5: Écrire la surcouche de développement**

Créer `stack/docker-compose.dev.yml` :

```yaml
# NON DEPLOYE — hooks/install.py ne copie pas ce fichier.
#
# Il monte les integrations en cours de developpement depuis les depots de la
# machine de Maxime. Sur toute autre machine ces chemins n'existent pas, et
# docker creerait des repertoires vides que Home Assistant chargerait comme
# des integrations cassees.
#
# Usage sur la machine de dev :
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
#
# A mesure que les satellites existent (home-stock, home-desk), leur bind
# disparait d'ici : c'est leur propre hooks/install.py qui depose le
# custom_component dans config/custom_components/.
services:
  homeassistant:
    volumes:
      - /home/mallanic/Projects/HA-AI/custom_components/ha_ai_learner:/config/custom_components/ha_ai_learner
      - /home/mallanic/Projects/Nivuus/marketplace/custom_components/docker_marketplace:/config/custom_components/docker_marketplace
      - /home/mallanic/Projects/Nivuus/home_agent:/config/custom_components/home_agent
      - /home/mallanic/Projects/Nivuus/marketplace/catalog:/config/catalog
      - /opt/nivuus/HomeAssistant/data/meal/custom_components/home_stock:/config/custom_components/home_stock
```

- [ ] **Step 6: Écrire le gabarit d'environnement**

Créer `stack/env.template` :

```bash
# =============================================================================
# home-manager — rendu par hooks/install.py. Ne pas editer a la main les
# valeurs pilotees par le wizard : une reinstallation les recalcule.
# Ce fichier n'est JAMAIS ecrase : les cles manquantes sont ajoutees, les
# presentes sont laissees telles quelles.
# =============================================================================

TZ=@TZ@

# =============================================================================
# Passerelle radio
# Par defaut un SLZB-MR2U joint par le reseau (ser2net) : deux radios, deux
# ports. En mode USB (COMPOSE_FILE inclut docker-compose.usb.yml), ce sont
# THREAD_DEVICE et ZIGBEE_DEVICE qui font foi.
# =============================================================================
SLZB_HOST=@SLZB_HOST@
THREAD_PORT=@THREAD_PORT@
ZIGBEE_PORT=@ZIGBEE_PORT@
RCP_BAUDRATE=@RCP_BAUDRATE@
THREAD_DEVICE=@THREAD_DEVICE@
ZIGBEE_DEVICE=@ZIGBEE_DEVICE@

# Puissance d'emission Thread, en dBm. 20 est le maximum du SLZB-MR2U et reste
# dans la limite ETSI (100 mW). otbr-agent ne la persiste pas : le command du
# service la reapplique en boucle.
THREAD_TXPOWER=@THREAD_TXPOWER@

# Interface reseau que la bordure Thread et le controleur Matter utilisent
# comme backbone. C'est le pont du LAN, pas l'interface physique.
BACKBONE_IF=@BACKBONE_IF@

# =============================================================================
# Composition de la pile — la surcouche USB n'est fusionnee qu'en mode USB.
# =============================================================================
COMPOSE_FILE=@COMPOSE_FILE@
```

- [ ] **Step 7: Lancer le test pour vérifier qu'il passe**

Run: `python3 tests/test_compose_portable.py`
Expected: PASS — `test_compose_portable: OK`

- [ ] **Step 8: Vérifier que le compose est syntaxiquement valide**

Run:
```bash
cd stack && SLZB_HOST=192.168.0.79 THREAD_PORT=6638 ZIGBEE_PORT=7638 \
  RCP_BAUDRATE=460800 THREAD_TXPOWER=20 BACKBONE_IF=localBridge TZ=Europe/Paris \
  docker compose -f docker-compose.yml config --quiet && echo "compose valide"
```
Expected: `compose valide`, sans avertissement de variable non définie.

- [ ] **Step 9: Commit**

```bash
git add stack/docker-compose.yml stack/docker-compose.usb.yml \
        stack/docker-compose.dev.yml stack/env.template \
        tests/test_compose_portable.py
git commit -m "feat(stack): les six services domotiques dans un compose portable"
```

---

### Task 3 : le wizard et la validation des réponses

Les constantes de cette maison deviennent des questions. Sans cette tâche, le package n'est installable que chez son auteur.

**Files:**
- Create: `wizard.yaml`, `tests/test_wizard_answers.py`

**Interfaces:**
- Consumes: les variables déclarées par `stack/env.template` (Task 2).
- Produces: les clés de réponses lues par `hooks/install.py` (Task 4) — `radio_mode` (`reseau`|`usb`), `slzb_host`, `thread_port`, `zigbee_port`, `rcp_baudrate`, `thread_device`, `zigbee_device`, `thread_txpower`, `backbone_if`, `timezone`, `mqtt_password`.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_wizard_answers.py` :

```python
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

# Le mode USB doit rester possible : le materiel de reference n'est pas une
# hypothese universelle.
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
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 tests/test_wizard_answers.py`
Expected: FAIL — `FileNotFoundError: wizard.yaml`

- [ ] **Step 3: Écrire le wizard**

Créer `wizard.yaml` :

```yaml
# Les valeurs par defaut sont celles de l'hote de reference : une installation
# chez son auteur se fait en validant le formulaire tel quel. Elles n'en sont
# pas moins des QUESTIONS — ecrites en dur dans le compose, elles rendaient le
# package installable chez une seule personne.

- key: radio_mode
  type: choix
  label: "Radios Thread et Zigbee : passerelle reseau ou peripheriques USB"
  # « choices », en anglais : c'est la cle que lit packages/wizard.py. Un
  # « choix: » ici ferait une question de type choix sans aucun choix, que le
  # moteur refuse au chargement.
  choices: [reseau, usb]
  default: reseau
  required: true

- key: slzb_host
  type: texte
  label: "Adresse de la passerelle radio (mode reseau)"
  default: "192.168.0.79"

- key: thread_port
  type: texte
  label: "Port ser2net de la radio Thread"
  default: "6638"

- key: zigbee_port
  type: texte
  label: "Port ser2net de la radio Zigbee"
  default: "7638"

- key: rcp_baudrate
  type: texte
  label: "Debit de la liaison Thread (baud)"
  default: "460800"

- key: thread_device
  type: texte
  label: "Peripherique Thread (mode USB uniquement)"
  default: "/dev/ttyACM0"

- key: zigbee_device
  type: texte
  label: "Peripherique Zigbee (mode USB uniquement)"
  default: "/dev/ttyACM1"

# 20 dBm est le maximum du SLZB-MR2U et reste sous la limite ETSI de 100 mW.
# La valeur compte : un enfant Thread a -77 dBm perdait des trames en
# downlink au defaut de 0 dBm.
- key: thread_txpower
  type: texte
  label: "Puissance d'emission Thread (dBm)"
  default: "20"

# L'interface que la bordure Thread et le controleur Matter annoncent comme
# backbone. C'est le pont du LAN, pas la carte physique.
- key: backbone_if
  type: texte
  label: "Interface reseau du backbone (pont LAN)"
  default: "localBridge"

- key: timezone
  type: texte
  label: "Fuseau horaire"
  default: "Europe/Paris"

# Sans defaut, deliberement : un mot de passe par defaut partage par toutes
# les installations est pire que pas de mot de passe. Vide, hooks/install.py
# n'ecrit aucun compte et le broker garde ceux qu'il a deja.
- key: mqtt_password
  type: secret
  label: "Mot de passe du compte MQTT « mqtt » (vide = ne pas modifier)"
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `python3 tests/test_wizard_answers.py`
Expected: PASS — `test_wizard_answers: OK`

- [ ] **Step 5: Vérifier avec le validateur du moteur**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, '$HOME/Projects/Nivuus/packages/installer/installer')
from packages.wizard import load_questions
qs = load_questions('wizard.yaml')
print(f'{len(qs)} questions acceptées par le moteur')
"
```
Expected: `11 questions acceptées par le moteur`. Les types employés (`choix`, `texte`, `secret`) sont trois des six que `QUESTION_TYPES` déclare ; un échec ici signifie une faute de frappe dans `wizard.yaml`, pas une incompatibilité de contrat.

- [ ] **Step 6: Commit**

```bash
git add wizard.yaml tests/test_wizard_answers.py
git commit -m "feat(wizard): les constantes de la passerelle radio deviennent des questions"
```

---

### Task 4 : le hook d'installation

Déposer la pile sans jamais détruire une donnée. C'est la tâche où une erreur coûte une maison entière de configuration.

**Files:**
- Create: `hooks/install.py`, `stack/mosquitto/mosquitto.conf`, `stack/config/configuration.yaml`, `tests/test_install_hook.py`

**Interfaces:**
- Consumes: les réponses du wizard (Task 3), `stack/` (Task 2).
- Produces: un déploiement dans `{root}/opt/nivuus/home-manager` contenant `docker-compose.yml`, `.env` (0600), `mosquitto/mosquitto.conf`, et `config/configuration.yaml` s'il était absent. Consommé par `hooks/activate.py` (Task 5).

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_install_hook.py` :

```python
#!/usr/bin/env python3
"""La phase install ne doit JAMAIS detruire une donnee.

Ce hook tourne aussi sur une machine deja installee (`install.py --root /`),
ou config/ porte les automations, la base et les secrets d'une maison entiere.
Chaque assertion ici garde une chose qu'une reinstallation pourrait effacer
sans bruit.

Run: python3 tests/test_install_hook.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "install.py"
DEST_REL = "opt/nivuus/home-manager"

ANSWERS = {
    "radio_mode": "reseau",
    "slzb_host": "10.0.0.5",
    "thread_port": "6638",
    "zigbee_port": "7638",
    "rcp_baudrate": "460800",
    "thread_device": "/dev/ttyACM0",
    "zigbee_device": "/dev/ttyACM1",
    "thread_txpower": "20",
    "backbone_if": "br0",
    "timezone": "Europe/Paris",
    "mqtt_password": "",
}

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def run(root, answers=None):
    ctx = json.dumps({"answers": answers if answers is not None else ANSWERS})
    return subprocess.run(
        [sys.executable, str(HOOK), "--phase", "install", "--root", str(root)],
        input=ctx, capture_output=True, text=True)


def env_values(path):
    values = {}
    for line in pathlib.Path(path).read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


# --- installation neuve --------------------------------------------------
with tempfile.TemporaryDirectory() as root:
    proc = run(root)
    check("installation neuve reussit", proc.returncode, 0)

    dest = pathlib.Path(root) / DEST_REL
    check("le compose est depose", (dest / "docker-compose.yml").is_file(), True)
    check("la surcouche USB est deposee",
          (dest / "docker-compose.usb.yml").is_file(), True)

    # Le fichier de dev monte des chemins qui n'existent que chez son auteur :
    # le deposer creerait des integrations cassees sur toute autre machine.
    check("le fichier de developpement n'est PAS depose",
          (dest / "docker-compose.dev.yml").exists(), False)

    # Le gabarit a fait son travail ; le laisser a cote du .env rendu ne
    # laisserait pas savoir lequel fait foi.
    check("le gabarit n'est pas laisse sur place",
          (dest / "env.template").exists(), False)

    values = env_values(dest / ".env")
    check("l'hote de la passerelle vient des reponses",
          values["SLZB_HOST"], "10.0.0.5")
    check("l'interface backbone vient des reponses",
          values["BACKBONE_IF"], "br0")
    check("mode reseau : pas de surcouche USB",
          values["COMPOSE_FILE"], "docker-compose.yml")

    # 0600 : le .env porte le mot de passe du broker.
    check("le .env n'est lisible que par root",
          oct((dest / ".env").stat().st_mode & 0o777), "0o600")

    check("le bootstrap de Home Assistant est cree",
          (dest / "config" / "configuration.yaml").is_file(), True)
    check("la configuration du broker est deposee",
          (dest / "mosquitto" / "mosquitto.conf").is_file(), True)

    # zigbee2mqtt lit son port dans SON fichier, pas dans l'environnement : le
    # bootstrap doit donc porter la reponse du wizard.
    z2m = (dest / "zigbee2mqtt" / "configuration.yaml").read_text()
    check("le bootstrap zigbee2mqtt porte le port repondu",
          "tcp://10.0.0.5:7638" in z2m, True)

# --- mode USB ------------------------------------------------------------
with tempfile.TemporaryDirectory() as root:
    run(root, {**ANSWERS, "radio_mode": "usb"})
    values = env_values(pathlib.Path(root) / DEST_REL / ".env")
    check("mode USB : la surcouche est fusionnee",
          values["COMPOSE_FILE"],
          "docker-compose.yml:docker-compose.usb.yml")

# --- reinstallation sur une production vivante ---------------------------
with tempfile.TemporaryDirectory() as root:
    dest = pathlib.Path(root) / DEST_REL
    (dest / "config").mkdir(parents=True)
    (dest / "mosquitto").mkdir(parents=True)
    (dest / "zigbee2mqtt").mkdir(parents=True)

    # Ce que porte une vraie installation, et qui ne doit pas bouger.
    (dest / "config" / "configuration.yaml").write_text("# 400 automations\n")
    (dest / "config" / "secrets.yaml").write_text("latitude: 48.85\n")
    (dest / "config" / "home-assistant_v2.db").write_text("base sqlite")
    (dest / "mosquitto" / "passwd").write_text("mqtt:$7$hash\n")
    # LE fichier le plus dangereux du lot : il porte network_key et pan_id.
    # L'ecraser ne casse pas zigbee2mqtt, il forme un AUTRE reseau — et les
    # quelques dizaines d'equipements apparies restent sur l'ancien, muets.
    (dest / "zigbee2mqtt" / "configuration.yaml").write_text(
        "advanced:\n  network_key: [1, 2, 3]\n  pan_id: 28047\n")
    (dest / ".env").write_text("TZ=America/Denver\nSLZB_HOST=1.2.3.4\n")

    proc = run(root)
    check("reinstallation reussit", proc.returncode, 0)

    check("configuration.yaml intact",
          (dest / "config" / "configuration.yaml").read_text(),
          "# 400 automations\n")
    check("secrets.yaml intact",
          (dest / "config" / "secrets.yaml").read_text(), "latitude: 48.85\n")
    check("base de donnees intacte",
          (dest / "config" / "home-assistant_v2.db").read_text(), "base sqlite")
    check("comptes du broker intacts",
          (dest / "mosquitto" / "passwd").read_text(), "mqtt:$7$hash\n")
    check("cles du reseau Zigbee intactes",
          (dest / "zigbee2mqtt" / "configuration.yaml").read_text(),
          "advanced:\n  network_key: [1, 2, 3]\n  pan_id: 28047\n")

    values = env_values(dest / ".env")
    check("les valeurs existantes du .env ne sont pas ecrasees",
          values["TZ"], "America/Denver")
    check("les valeurs existantes du .env ne sont pas ecrasees (2)",
          values["SLZB_HOST"], "1.2.3.4")
    check("les variables manquantes sont ajoutees",
          values["BACKBONE_IF"], "br0")

# --- reponses invalides --------------------------------------------------
with tempfile.TemporaryDirectory() as root:
    # Valider AVANT de deposer le premier octet : un appelant et le package en
    # desaccord sur le contrat est une erreur, pas quelque chose a contourner
    # au milieu d'une copie. Le chemin autonome (config.json ecrit a la main)
    # ne passe par aucun validateur.
    proc = run(root, {**ANSWERS, "radio_mode": "bluetooth"})
    check("un mode radio inconnu est refuse", proc.returncode, 1)
    check("rien n'a ete depose",
          (pathlib.Path(root) / DEST_REL).exists(), False)

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("test_install_hook: OK")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 tests/test_install_hook.py`
Expected: FAIL — le hook n'existe pas ; `proc.returncode` vaut 2 et non 0.

- [ ] **Step 3: Écrire la configuration du broker**

Créer `stack/mosquitto/mosquitto.conf` (reprise fidèle de la production, chemins inchangés) :

```conf
# Broker MQTT du package home-manager.
persistence true
persistence_location /mosquitto/data/
log_dest file /mosquitto/data/mosquitto.log
log_type error
log_type warning
log_type notice
log_type information
log_type subscribe
log_type unsubscribe

# Requis pour autoriser l'anonyme sur le SEUL listener 8883 : sans cette
# directive, allow_anonymous serait global et ouvrirait aussi 1883 et 1884.
per_listener_settings true

listener 1883
allow_anonymous false
password_file /mosquitto/data/passwd

listener 1884
allow_anonymous false
password_file /mosquitto/data/passwd

# 8883 : TLS, requis par les ampoules Meross en liaison locale — elles
# n'acceptent que MQTTS en TLS 1.2 et ne valident pas la CA, d'ou le
# certificat auto-signe. Elles s'authentifient avec leur MAC, qui contient des
# ':' : impossible a stocker dans un password_file au format « user:hash ».
# D'ou l'anonyme, borne par ACL aux seuls topics Meross.
listener 8883
certfile /mosquitto/data/certs/server.crt
keyfile /mosquitto/data/certs/server.key
tls_version tlsv1.2
require_certificate false
allow_anonymous true
acl_file /mosquitto/data/acl_meross

listener 8884
allow_anonymous false
password_file /mosquitto/data/passwd

user mosquitto
```

- [ ] **Step 4: Écrire le bootstrap Home Assistant**

Créer `stack/config/configuration.yaml` :

```yaml
# Configuration de DEMARRAGE, deposee seulement si config/configuration.yaml
# est absent. Une installation vivante porte ici les automations, les
# dashboards et les integrations d'une maison entiere : ce fichier ne doit
# JAMAIS en ecraser un existant.
default_config:

# Le package ne versionne aucune configuration personnelle. Ces trois
# includes existent pour que l'interface puisse ecrire des le premier
# demarrage, sans quoi Home Assistant refuse d'enregistrer une automation.
automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml
```

- [ ] **Step 5: Écrire le bootstrap zigbee2mqtt**

Créer `stack/zigbee2mqtt/configuration.yaml` :

```yaml
# Configuration de DEMARRAGE de zigbee2mqtt, deposee seulement si le fichier
# est absent, puis rendue une fois par hooks/install.py.
#
# Passe ce premier passage, zigbee2mqtt REECRIT ce fichier lui-meme et y range
# le network_key, le pan_id et l'ext_pan_id du reseau qu'il forme. Ces valeurs
# sont l'identite du reseau : les remplacer n'affiche aucune erreur, cela forme
# simplement un AUTRE reseau, sur lequel aucun des equipements deja apparies
# ne se trouve. D'ou sa presence dans PRESERVED.
homeassistant:
  enabled: true

mqtt:
  base_topic: zigbee2mqtt
  # Le broker du meme package, sur la boucle locale : les deux conteneurs sont
  # en network_mode: host.
  server: mqtt://localhost:1883
  user: mqtt

serial:
  # Mode reseau : la radio Zigbee est exposee en ser2net par la passerelle.
  # En mode USB, remplacer par le peripherique — le wizard pose la question,
  # et seule cette ligne change.
  port: tcp://@SLZB_HOST@:@ZIGBEE_PORT@
  adapter: zstack
```

- [ ] **Step 6: Écrire le hook**

Créer `hooks/install.py` :

```python
#!/usr/bin/env python3
"""Phase install du package home-manager : deposer la pile sur la cible.

Le sous-arbre stack/ EST le repertoire de deploiement, a l'octet pres, donc la
depose est une copie recursive — il n'y a pas de liste de fichiers a tenir a
jour, et donc pas de fichier qu'on oublie d'ajouter en meme temps qu'un
service. Deux exceptions, retirees APRES la copie : le gabarit d'environnement
(il a fait son travail) et la surcouche de developpement.

TROIS REGLES PORTENT LE RESTE.

1. RIEN QUI PORTE DE LA DONNEE N'EST ECRASE. config/ contient les automations,
   la base, les secrets et les jetons d'une maison entiere ; mosquitto/passwd
   les comptes du broker ; .env le mot de passe MQTT. Cette phase tourne aussi
   sur une machine deja installee (`install.py --root /`), ou une reecriture
   detruirait tout cela sans bruit. Les cles absentes du .env sont AJOUTEES,
   les presentes ne sont pas touchees ; les fichiers existants sont sautes.

2. LES REPONSES SONT VALIDEES AVANT LA PREMIERE ECRITURE. Le hook lit son
   contexte sur stdin, et le chemin autonome que le contrat existe pour
   permettre — un config.json ecrit a la main — ne passe par aucun validateur.
   Un mode radio inconnu doit faire echouer la phase avant la copie, pas au
   milieu.

3. LE FICHIER DE DEVELOPPEMENT NE PART PAS. docker-compose.dev.yml monte des
   depots qui n'existent que sur la machine de son auteur ; docker creerait
   ailleurs des repertoires vides que Home Assistant chargerait comme des
   integrations cassees.
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

# Fichiers qui portent de la donnee : jamais ecrases s'ils existent deja.
# Chemins relatifs au repertoire de deploiement.
#
# zigbee2mqtt/configuration.yaml est le plus dangereux des trois : il porte le
# network_key et le pan_id du reseau. L'ecraser ne « casse » pas zigbee2mqtt,
# il en forme un AUTRE — et tous les equipements apparies restent sur
# l'ancien, muets, sans le moindre message d'erreur.
PRESERVED = (
    "config/configuration.yaml",
    "mosquitto/mosquitto.conf",
    "zigbee2mqtt/configuration.yaml",
)

# Fichiers rendus depuis le gabarit apres la copie, avec les memes @CLES@ que
# le .env. Ceux de PRESERVED ne sont rendus que lorsqu'ils viennent d'etre
# crees : zigbee2mqtt lit son port dans SON fichier, pas dans l'environnement,
# donc la reponse du wizard doit y etre ecrite au premier passage — et jamais
# au suivant.
RENDERED = ("zigbee2mqtt/configuration.yaml",)


def emit(event):
    print(json.dumps(event), flush=True)


def text_answer(answers, key, default=""):
    value = answers.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"la reponse {key!r} attend une chaine, recu {value!r}")
    return value.strip() or default


def parse_env(text):
    """Les cles definies dans un .env, dans l'ordre de lecture."""
    keys = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            keys.append(stripped.partition("=")[0].strip())
    return keys


def merge_env(existing, rendered):
    """Le .env existant, augmente des seules cles qu'il n'a pas encore.

    Rien d'existant n'est touche : ni les valeurs, ni les commentaires, ni
    l'ordre. Les cles nouvelles sont ajoutees en fin de fichier sous un
    en-tete qui dit d'ou elles viennent.
    """
    have = set(parse_env(existing))
    added = [line for line in rendered.splitlines()
             if "=" in line and not line.strip().startswith("#")
             and line.partition("=")[0].strip() not in have]
    if not added:
        return existing
    tail = "\n# --- Ajoute par le package home-manager ---\n" + "\n".join(added)
    return existing.rstrip("\n") + "\n" + tail + "\n"


def render_env(values):
    with open(os.path.join(STACK, TEMPLATE_NAME)) as fh:
        text = fh.read()
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    return text


def copy_stack(dest):
    """stack/ vers dest sans ecraser PRESERVED. Rend les nouveaux fichiers.

    Retourne l'ensemble des chemins relatifs de PRESERVED qui ont ete CREES
    par cette copie — ceux qui existaient deja n'y sont pas. L'appelant s'en
    sert pour ne rendre les gabarits qu'au premier passage.
    """
    existing = {rel for rel in PRESERVED
                if os.path.isfile(os.path.join(dest, rel))}
    saved = {}
    for rel in existing:
        with open(os.path.join(dest, rel), "rb") as fh:
            saved[rel] = fh.read()

    shutil.copytree(STACK, dest, symlinks=True, dirs_exist_ok=True)

    # Restaure ce que la copie vient d'ecraser. Sauver puis restaurer, plutot
    # que filtrer la copie : copytree n'offre pas de « ne pas ecraser », et un
    # ignore= sur les noms sauterait aussi le fichier lors d'une installation
    # neuve, ou il doit bien etre depose.
    for rel, content in saved.items():
        with open(os.path.join(dest, rel), "wb") as fh:
            fh.write(content)

    for name in (TEMPLATE_NAME, DEV_OVERLAY):
        path = os.path.join(dest, name)
        if os.path.exists(path):
            os.remove(path)

    return set(PRESERVED) - existing


def render_file(path, values):
    """Remplacer les @CLES@ d'un fichier depose, sur place."""
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

    # Regle 2 : valider avant de deposer le premier octet.
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

    # La surcouche USB n'est fusionnee qu'en mode USB : c'est une consequence
    # du mode radio, pas une question de plus.
    compose_files = "docker-compose.yml"
    if radio_mode == "usb":
        compose_files += ":docker-compose.usb.yml"
    values["COMPOSE_FILE"] = compose_files

    dest = os.path.join(root, DEST_REL)

    emit({"event": "progress", "pct": 25, "msg": "Depose de la pile"})
    created = copy_stack(dest)

    # Les gabarits deposes ne sont rendus qu'au premier passage : un fichier
    # deja present porte la configuration reelle, et y reinjecter les reponses
    # du wizard remplacerait le reseau Zigbee en service par un neuf.
    for rel in RENDERED:
        if rel in created:
            render_file(os.path.join(dest, rel), values)

    emit({"event": "progress", "pct": 65, "msg": "Rendu de la configuration"})
    rendered = render_env(values)
    env_path = os.path.join(dest, ".env")
    if os.path.isfile(env_path):
        with open(env_path) as fh:
            rendered = merge_env(fh.read(), rendered)
        emit({"event": "progress", "pct": 75,
              "msg": ".env existant conserve, variables manquantes ajoutees"})
    with open(env_path, "w") as fh:
        fh.write(rendered)
    # 0600 : le .env porte le mot de passe du broker.
    os.chmod(env_path, 0o600)

    emit({"event": "progress", "pct": 90,
          "msg": f"Domotique deposee dans /{DEST_REL}"})
    emit({"event": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Rendre le hook exécutable**

```bash
chmod +x hooks/install.py
```

- [ ] **Step 8: Lancer le test pour vérifier qu'il passe**

Run: `python3 tests/test_install_hook.py`
Expected: PASS — `test_install_hook: OK`

- [ ] **Step 9: Commit**

```bash
git add hooks/install.py stack/mosquitto/mosquitto.conf \
        stack/config/configuration.yaml stack/zigbee2mqtt/configuration.yaml \
        tests/test_install_hook.py
git commit -m "feat(hooks): phase install, aucune donnee existante ecrasee"
```

---

### Task 5 : le hook d'activation

Démarrer la pile au premier boot, sans ressusciter ce que quelqu'un d'autre a délibérément arrêté.

**Files:**
- Create: `hooks/activate.py`, `tests/test_activate_hook.py`

**Interfaces:**
- Consumes: le déploiement produit par `hooks/install.py` (Task 4).
- Produces: rien de consommé par du code ; six conteneurs démarrés.

- [ ] **Step 1: Écrire le test qui échoue**

Créer `tests/test_activate_hook.py` :

```python
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
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 tests/test_activate_hook.py`
Expected: FAIL — `FileNotFoundError: hooks/activate.py`

- [ ] **Step 3: Écrire le hook**

Créer `hooks/activate.py` :

```python
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
```

- [ ] **Step 4: Rendre le hook exécutable**

```bash
chmod +x hooks/activate.py
```

- [ ] **Step 5: Lancer le test pour vérifier qu'il passe**

Run: `python3 tests/test_activate_hook.py`
Expected: PASS — `test_activate_hook: OK`

- [ ] **Step 6: Lancer la totalité de la suite**

Run: `make test NIVUUS_INSTALLER_DIR=$HOME/Projects/Nivuus/packages/installer`
Expected: cinq blocs, tous `OK`.

- [ ] **Step 7: Commit**

```bash
git add hooks/activate.py tests/test_activate_hook.py
git commit -m "feat(hooks): phase activate, le broker demarre avant ses consommateurs"
```

---

### Task 6 : installation à blanc et documentation

Le package est écrit ; reste à prouver qu'il s'installe et à expliquer ce qu'il fait.

**Files:**
- Create: `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien de consommé par du code.

- [ ] **Step 1: Installer à blanc dans un répertoire temporaire**

Run:
```bash
BLANC=$(mktemp -d)
echo '{"answers":{"radio_mode":"reseau","slzb_host":"192.168.0.79",
"thread_port":"6638","zigbee_port":"7638","rcp_baudrate":"460800",
"thread_device":"/dev/ttyACM0","zigbee_device":"/dev/ttyACM1",
"thread_txpower":"20","backbone_if":"localBridge",
"timezone":"Europe/Paris","mqtt_password":""}}' \
  | python3 hooks/install.py --phase install --root "$BLANC"
find "$BLANC" -type f | sort
```
Expected: les fichiers du déploiement, **sans** `env.template` ni `docker-compose.dev.yml`. Noter le chemin `$BLANC` pour l'étape suivante.

- [ ] **Step 2: Vérifier que le compose déposé est exploitable**

Run:
```bash
cd "$BLANC/opt/nivuus/home-manager" && docker compose config --quiet \
  && echo "compose deploye valide" && grep -c . .env
```
Expected: `compose deploye valide`, et un `.env` non vide. Aucun avertissement `variable is not set`.

- [ ] **Step 3: Vérifier l'idempotence**

Run:
```bash
echo '{"answers":{"radio_mode":"reseau","slzb_host":"192.168.0.79",
"thread_port":"6638","zigbee_port":"7638","rcp_baudrate":"460800",
"thread_device":"/dev/ttyACM0","zigbee_device":"/dev/ttyACM1",
"thread_txpower":"20","backbone_if":"localBridge",
"timezone":"Europe/Paris","mqtt_password":""}}' \
  | python3 hooks/install.py --phase install --root "$BLANC" \
  && echo "deuxieme passage OK" && rm -rf "$BLANC"
```
Expected: `deuxieme passage OK`, sans erreur.

- [ ] **Step 4: Écrire le README**

Créer `README.md` :

```markdown
# home-manager — package Nivuus

La domotique de la maison : Home Assistant et les quatre services dont il
dépend, en un seul paquet installable.

## Ce qu'il installe

| Service | Rôle |
|---|---|
| `homeassistant` | le hub, sur le port 8123 |
| `mosquitto` | broker MQTT (1883, 1884, 8883 TLS, 8884) |
| `zigbee2mqtt` | réseau Zigbee |
| `otbr` | bordure Thread (OpenThread Border Router) |
| `matterjs-server` | contrôleur Matter |
| `docker-socket-proxy` | API Docker filtrée, seule voie d'accès de HA au démon |

Déploiement : `/opt/nivuus/home-manager`.

## Radios

Par défaut les radios Thread et Zigbee sont jointes **par le réseau**, sur une
passerelle de type SLZB-MR2U exposant deux ports ser2net. Le wizard demande son
adresse et ses ports. En mode USB, la surcouche `docker-compose.usb.yml` est
fusionnée et les deux périphériques sont montés directement.

## Socle d'une famille

Ce package est le socle des satellites domotiques (tablettes, stocks). Un
satellite déclare dans son manifeste :

```yaml
requires:
  packages: [home-manager]
```

et dépose son intégration dans `/opt/nivuus/home-manager/config/custom_components/`.
Le moteur garantit alors que le socle est installé d'abord.

## Ce qu'il ne fait pas

- il ne versionne **aucune** configuration Home Assistant : `config/` est de la
  donnée, et une réinstallation n'y touche pas ;
- il n'embarque pas ESPHome ;
- il n'embarque pas le watchdog OTBR, désactivé en production le 2026-05-04
  (213 redémarrages de passerelle par semaine, aucune récupération).

## Tests

```bash
make test NIVUUS_INSTALLER_DIR=$HOME/Projects/Nivuus/packages/installer
```

Sans `NIVUUS_INSTALLER_DIR`, le manifeste est vérifié localement au lieu de
l'être par le parseur du moteur.
```

- [ ] **Step 5: Écrire le CLAUDE.md**

Créer `CLAUDE.md` :

```markdown
# home-manager — notes d'implémentation

## Chemins critiques

- Déploiement : `/opt/nivuus/home-manager`. Toute donnée est montée en
  **relatif** dans le compose ; un chemin absolu réintroduit le couplage à la
  machine que ce package existe pour supprimer.
- `config/` porte les automations, la base, `secrets.yaml` et les jetons. Le
  hook d'installation ne l'écrase jamais — voir `PRESERVED` dans
  `hooks/install.py`.

## Décisions à ne pas défaire

- **mosquitto appartient à ce package**, pas au `docker_marketplace` de Home
  Assistant. Avant, HA lançait le broker dont HA dépend : si HA ne démarrait
  pas, ni le broker ni zigbee2mqtt ne remontaient. Le hook d'activation
  démarre le broker en premier pour la même raison.
- **`matterjs-server`** (image `ghcr.io/matter-js/matterjs-server`) remplace
  `matter-server` (`python-matter-server`) depuis juillet 2026. Ne pas
  confondre : le second n'existe plus que comme conteneur arrêté sur l'hôte de
  référence.
- **Le txpower Thread est réappliqué en boucle** dans le `command` du service
  `otbr`. `otbr-agent` ne le persiste pas : retirer la boucle, c'est revenir
  silencieusement à 0 dBm et perdre les enfants Thread lointains.
- **`OTBR_RCP_ADDITIONAL_ARGS` doit rester vide.** Le défaut de l'image
  (`&uart-flow-control`) bloque la transmission vers l'EFR32 du SLZB, dont le
  contrôle de flux matériel est désactivé : la réception fonctionne, les
  commandes ne parviennent jamais.
- **`zigbee2mqtt/configuration.yaml` n'est jamais réécrit après le premier
  passage.** zigbee2mqtt y range le `network_key`, le `pan_id` et
  l'`ext_pan_id` du réseau qu'il forme. Les remplacer n'affiche aucune erreur :
  cela forme un *autre* réseau, sur lequel aucun des équipements appariés ne se
  trouve. C'est la raison d'être de `RENDERED` dans `hooks/install.py` — le
  gabarit n'est rendu que sur un fichier que la copie vient de créer.
- **`per_listener_settings true`** dans `mosquitto.conf` : sans cette
  directive, l'`allow_anonymous` du listener 8883 (requis par les ampoules
  Meross) deviendrait global et ouvrirait aussi 1883 et 1884.
- **Le watchdog OTBR reste dehors**, désactivé en production le 2026-05-04 :
  213 redémarrages de passerelle par semaine pour zéro récupération.

## Style

Scripts de test autonomes lancés par `make test`, pas de pytest, pas de
dépendance hors `python3` + PyYAML — c'est le style du dépôt `installer`.
```

- [ ] **Step 6: Lancer la suite complète une dernière fois**

Run: `make test NIVUUS_INSTALLER_DIR=$HOME/Projects/Nivuus/packages/installer`
Expected: cinq blocs, tous `OK`.

- [ ] **Step 7: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: README et notes d'implementation du package home-manager"
```

---

## Vérification finale

- [ ] `make test NIVUUS_INSTALLER_DIR=…` : les cinq suites passent.
- [ ] Le manifeste est accepté par le **vrai** parseur du moteur.
- [ ] Une installation à blanc dans un `mktemp -d` produit un compose que
      `docker compose config` valide, sans variable non résolue.
- [ ] Une seconde installation sur ce même répertoire ne modifie ni
      `configuration.yaml`, ni `secrets.yaml`, ni les valeurs déjà présentes
      dans le `.env`.
- [ ] `grep -r "/home/mallanic" stack/` ne remonte **que**
      `docker-compose.dev.yml`.
- [ ] `grep -r "/opt/nivuus/HomeAssistant" stack/` ne remonte **que**
      `docker-compose.dev.yml` (le bind `home_stock`, en attendant le
      satellite).
- [ ] Aucun conteneur de production n'a été démarré, arrêté ni modifié par ce
      lot.
