# HomeAssistant en package Nivuus `home-manager` — design

**Date** : 2026-08-28
**Statut** : validé, prêt pour le plan d'implémentation

## Objectif

Sortir la domotique de `/opt/nivuus/HomeAssistant` — où elle est à la fois
dépôt source, répertoire de production et dépotoir de sauvegardes — et en faire
un **package Nivuus** `home-manager` sous
`~/Projects/Nivuus/packages/home-manager`, installable par l'installer au même
titre que `console` et `media-manager`.

Le package est en outre le **socle** d'une famille : des packages satellites
(affichage des tablettes, stocks de la maison) viendront s'y greffer. Le moteur
n'ayant aujourd'hui aucune notion de dépendance entre packages, ce design
étend le contrat `nivuus.dev/v1` pour la lui donner.

Trois natures cohabitent aujourd'hui dans le même dossier :

| | Contenu | Volume |
|---|---|---|
| **Source** | 3 composes, conf mosquitto, scripts start/stop | ~30 Ko |
| **État** | `config/`, `media/`, `otbr/`, `zigbee2mqtt/`, `mosquitto/`, `matter_js/` | ~4,2 Go |
| **Déchets** | ~30 `*.backup-*`, `matter.bak.*`, `backup_*`, `models/`, `diyhue/`, `xiaomi-scale/`, `data/` | ~1,9 Go |

Le package ne porte que la première. La deuxième survit à la bascule par un
`mv`. La troisième reste sur place et n'est connue de personne.

## Le contrat que le package doit respecter

`installer/packages/` implémente `nivuus.dev/v1`. Ce que le moteur attend, et
qui contraint tout ce qui suit :

- un `nivuus-package.yaml` **à la racine du dépôt** — `iso-build/build.sh`
  saute tout répertoire qui n'en a pas ;
- l'export vers l'ISO se fait par `git archive HEAD` : **seuls les fichiers
  suivis par git voyagent**. Un fichier oublié dans `.gitignore` n'existe pas
  pour l'installer ;
- trois phases facultatives — `resolve` (avant toute écriture, lecture seule),
  `install` (écrit sous `--root`), `activate` (après redémarrage, réseau
  disponible) ; chaque hook lit un contexte JSON sur stdin et émet du jsonl ;
- `tier: userspace` interdit `kernel-cmdline`, `modules` et `hugepages-mib` ;
- la découverte se fait sous `/opt/nivuus-packages/*/` (surchargeable par
  `NIVUUS_PACKAGES_DIR`).

## Périmètre du socle

Six services, un seul compose :

| Service | Aujourd'hui | Rôle |
|---|---|---|
| `homeassistant` | `docker-compose.yml` | le hub |
| `otbr` | `docker-compose.yml` | bordure Thread, radio 1 du SLZB-MR2U en TCP |
| `zigbee2mqtt` | `docker-compose.yml` | Zigbee, radio 2 du SLZB-MR2U en TCP |
| `docker-socket-proxy` | `docker-socket-proxy/docker-compose.yml` | API Docker filtrée pour HA |
| `mosquitto` | **docker_marketplace de HA** (`config/stacks/mosquitto/`) | broker MQTT |
| `matterjs-server` | **rien du tout** (lancé à la main) | contrôleur Matter |

Deux de ces services ne sont pas décrits par un fichier du dépôt, et c'est le
gain principal du chantier, avant le rangement :

- `matterjs-server` ne porte **aucun label et aucun compose**. Il n'existe que
  dans le démon Docker. Un `docker system prune` malheureux ou une réinstall et
  il disparaît sans laisser de trace de ce qu'il était. (Son prédécesseur
  `matter-server`, lui, est encore déclaré au marketplace mais ne tourne plus.)
- `mosquitto` est géré par le **docker_marketplace de Home Assistant**
  (`docker_marketplace.managed: true`). C'est une dépendance circulaire réelle :
  HA lance le broker dont HA lui-même dépend, et `zigbee2mqtt` avec lui. Si HA
  ne démarre pas, la domotique ne remonte pas.

**Décision** : le socle reprend `mosquitto`, et la bascule supprime
`config/stacks/mosquitto/` pour le désenregistrer du marketplace. Un seul
gestionnaire, et le broker démarre avant son consommateur.

**Hors périmètre** : `esphome` (5,2 Go de builds, satellite possible plus
tard), `diyhue` et `xiaomi-scale` (plus référencés nulle part), `models/`,
toutes les sauvegardes datées.

**`otbr-watchdog.sh` n'entre pas dans le package**, et c'est délibéré. Le
crontab porte la raison de sa désactivation, le 2026-05-04 : *« causes Thread
network instability (213 SLZB reboots/week, 0 recoveries) »*. Zéro
récupération pour 213 redémarrages hebdomadaires du SLZB — le remède était pire
que le mal. Il vise de surcroît `matter-server`, conteneur arrêté depuis six
semaines et remplacé par `matterjs-server`. Le mettre dans le package
l'armerait à nouveau, sur toutes les machines, avec un bug supplémentaire.
Le crash Matter/OTBR qu'il traitait reste un problème ouvert, à reprendre pour
lui-même.

`data/` (701 Mo) mérite sa propre mention : ce n'est pas de l'état du socle
mais un dépotoir de travail — sauvegardes d'automations datées, diagnostics de
tablette, `events.db`, documents. Un seul de ses chemins est monté par HA,
`data/meal/custom_components/home_stock`, et il part au satellite `home-stock`.
Le répertoire reste donc sur place, et le bind qui le vise vit dans
`docker-compose.dev.yml` jusqu'à ce que ce satellite existe.

## Structure

```
packages/home-manager/
├── nivuus-package.yaml
├── wizard.yaml
├── hooks/
│   ├── install.py
│   └── activate.py
├── stack/                          ⟵ copié tel quel vers le répertoire de déploiement
│   ├── docker-compose.yml          # les six services
│   ├── docker-compose.usb.yml      # overlay radios USB (défaut : TCP)
│   ├── docker-compose.dev.yml      # NON déployé : binds /home/mallanic (machine de dev)
│   ├── env.template
│   ├── config/configuration.yaml   # bootstrap minimal, créé seulement s'il manque
│   ├── zigbee2mqtt/configuration.yaml  # idem — porte le network_key en prod
│   └── mosquitto/mosquitto.conf
├── tests/
├── Makefile
├── CLAUDE.md
└── README.md
```

Répertoire de déploiement : `/opt/nivuus/home-manager`.

### Manifeste

`tier: userspace`. Pas de `platform:`, pas de `claims:` — la domotique ne
réclame aucun matériel de façon exclusive, et un claim sur les radios les
rendrait mutuellement exclusives avec un futur satellite qui les partagerait.

Pas de `requires.features: [docker]`, par le précédent posé par `console` avec
firewalld : **un package déclare ses propres dépendances** plutôt que de se
coupler à la liste de features du moteur, sinon l'installation autonome sur une
Debian ordinaire casse. `apt:` porte donc `docker.io` et `docker-compose-v2`.

### Ce que le wizard demande

Le compose actuel est truffé de constantes propres à cette maison. Sans le
wizard, le package n'est installable que chez son auteur :

- adresse et ports des radios du SLZB-MR2U (`192.168.0.79:6638` Thread,
  `:7638` Zigbee), avec un mode USB en alternative ;
- `RCP_BAUDRATE` (460800) et `txpower` Thread (20 dBm, maximum ETSI du
  SLZB-MR2U) ;
- interface backbone (`localBridge`) ;
- fuseau horaire ;
- mot de passe du broker MQTT (type `secret`).

### Le contrat satellite

`/opt/nivuus/home-manager/config/custom_components/<nom>` est le point de
dépôt. Le `hooks/install.py` d'un satellite y copie son composant ; le socle ne
connaît aucun satellite par son nom.

Les binds `/home/mallanic/Projects/…` (`ha_ai_learner`, `docker_marketplace`,
`home_agent`, `home_stock`) **disparaissent du compose de production** et
migrent dans `docker-compose.dev.yml`, non déployé. Sur la machine de dev ils
restent actifs ; sur une installation neuve ils n'existent pas.

### Config Home Assistant

Le package **ne versionne rien** de `config/` : c'est de la donnée
d'exécution. Il fournit un `configuration.yaml` minimal, créé **seulement s'il
n'existe pas** — la règle du `.env` de `media-manager`, appliquée ici à un
fichier qui porte les automations d'une maison entière. Une réinstallation ne
doit jamais l'écraser.

## Extension du contrat : `requires.packages`

### Le problème

`plan_packages()` ordonne les packages par
`chosen = [by_name[name] for name in sorted(selected)]` — **ordre
alphabétique**. `home-desk` trie avant `home-manager` : un satellite
s'installerait avant son socle, et déposerait son custom_component dans un
répertoire qui n'existe pas encore.

### La forme retenue

Un champ déclaratif et un tri topologique. Rejetées : la dépendance par
convention (l'échec arriverait en phase install, disque déjà partitionné,
machine sans écran — exactement le mode de défaillance que `discovery.py` dit
vouloir éviter) et les capacités abstraites `provides:`/`requires:` (aucun
second fournisseur en vue ; le contrat v1 rappelle lui-même qu'un modèle de
ressources est plus facile à élargir qu'à rétrécir).

### `installer/packages/manifest.py`

- `Manifest` gagne `packages: tuple[str, ...] = ()`, alimenté par
  `requires.packages` ;
- chaque nom validé contre `NAME_RE` ; l'auto-dépendance est refusée ;
- **les clés inconnues sous `requires:` deviennent une erreur.** Aujourd'hui
  `requires:` accepte n'importe quelle clé en silence : un `requires: package:`
  au singulier — la faute de frappe évidente — serait ignoré, et le satellite
  s'installerait avant son socle sans que personne le sache. Le module annonce
  en tête qu'« une clé non reconnue à un endroit qui compte est une erreur,
  jamais un abandon silencieux » ; c'est ici qu'il faut tenir cette promesse.
  Durcissement sans victime : `console` et `media-manager` ne déclarent que
  `capabilities` et `features`.

### `installer/packages/dependencies.py` (neuf)

Sur le modèle exact de `conflicts.py` : un concept, un module, un dataclass
gelé, un message en français qui nomme les coupables.

- `missing_dependencies(chosen, catalog)` distingue deux cas qu'il ne faut pas
  confondre : le pré-requis **absent du support** et le pré-requis **présent
  mais non coché**. Le second est le cas courant et mérite son propre message.
- `install_order(chosen)` : tri topologique de Kahn, file d'attente triée
  alphabétiquement pour rester déterministe, `DependencyError` sur cycle en
  nommant le cycle.

### `installer/install-engine/steps/packages.py`

`chosen` reste construit par `sorted(selected)` comme point de départ, puis les
dépendances sont vérifiées et `install_order()` fixe l'ordre réel. Placé
**avant** `check_conflicts()` et avant les hooks `resolve`, donc avant le
partitionnement : l'échec arrive au wizard, pas sur un disque déjà effacé.

### `installer/webapp/main.py`

`describe(m)` expose `requires.packages` pour que le portail affiche
« nécessite : Home Manager ». **Pas d'auto-cochage** : cocher une case à la
place de l'opérateur sur une machine sans écran est un comportement magique,
hors périmètre v1.

## Bascule de la production

### Trois faits mesurés

1. `/opt` et `config/` sont sur le **même système de fichiers**
   (`nivuus--vg-root`). Le `mv` est un rename, pas une copie : l'interruption
   se compte en secondes, pas en 4,1 Go recopiés.
2. Seuls **deux fichiers YAML** portent le chemin absolu, et ce sont les
   composes générés du marketplace : celui de mosquitto disparaît avec la
   décision ci-dessus, celui de `matter-server` décrit un conteneur qui ne
   tourne plus.
3. **La sauvegarde restic de Home Assistant est morte.** Le cron de 5 h
   sauvegarde `/usr/share/hassio/homeassistant`, chemin qui **n'existe plus**
   depuis la migration Supervised → container. La production HA n'a aucune
   sauvegarde. Indépendant de ce chantier, mais pré-requis absolu à la bascule.

### Séquence

1. corriger le cron restic, prendre une première sauvegarde réelle ;
2. `docker compose down` ; `docker stop matterjs-server mosquitto` ;
3. `mv` des six répertoires d'état vers `/opt/nivuus/home-manager/` ;
4. `hooks/install.py --root /` dépose la stack, sans écraser une seule donnée ;
5. `hooks/activate.py` remonte les six conteneurs ;
6. vérifications : HA répond sur 8123, réseau Zigbee et dataset Thread reformés
   (le dataset vit dans `otbr/`, conservé par le `mv`), MQTT accepte une
   connexion ;
7. suppression de `config/stacks/mosquitto/`.

Le dossier d'origine est **renommé, jamais supprimé** : le rollback est le `mv`
inverse suivi de l'ancien compose.

## Tests

**Package** (style `media-manager` : scripts autonomes, `make test`, ni pytest
ni dépendance hors python3 + PyYAML) :

- `test_manifest_contract` — le manifeste passe le **vrai** parseur du moteur
  quand `NIVUUS_INSTALLER_DIR` est fourni, une revérification locale sinon ;
- `test_compose_portable` — les six services présents ; aucun chemin `/home/`
  ni `/opt/nivuus/HomeAssistant` résiduel dans le compose déployé ;
- `test_install_hook` — n'écrase jamais `config/`, `secrets.yaml` ni `.env` ;
  crée le bootstrap uniquement s'il manque ; idempotent ;
- `test_activate_hook` ;
- `test_wizard_answers` — les réponses radio TCP/USB produisent le bon overlay.

**Moteur** — `scripts/tests/test_packages_dependencies.py` (neuf) et complément
de `test_packages_manifest.py` : ordre topologique correct **et contraire à
l'alphabet** (`home-desk` avant `home-manager` est le bug réel), pré-requis non
coché, pré-requis absent du support, cycle, auto-dépendance, clé inconnue sous
`requires:`.

Aucun test ne touche la production : `--root` pointe vers un répertoire
temporaire.

## Ordre de réalisation

Trois lots livrables séparément :

1. **Extension du moteur** — `manifest.py`, `dependencies.py`, `packages.py`,
   `main.py` et leurs tests. Mergeable seule, ne change rien pour les packages
   existants.
2. **Package `home-manager`** — dépôt, manifeste, wizard, hooks, stack, tests.
   Validé sur `--root /tmp/…` sans toucher à la production.
3. **Bascule de la production** — décision distincte, après correction de
   restic.

## Ce que ce design ne fait pas

- il ne migre pas `esphome` ;
- il ne crée pas les packages satellites (`home-desk`, `home-stock`) : il leur
  ouvre la porte et fige le contrat qu'ils devront respecter ;
- il ne réécrit aucune automation ni aucun dashboard ;
- il ne supprime aucune des sauvegardes datées du dossier d'origine.
