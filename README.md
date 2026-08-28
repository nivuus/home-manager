# home-manager — package Nivuus

La domotique de la maison : Home Assistant et les cinq services dont il
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

Le port Zigbee ne passe pas par l'environnement : `zigbee2mqtt` le lit dans son
propre `configuration.yaml`, que le hook d'installation rend **une seule fois**.
Ce fichier porte ensuite le `network_key` du réseau et n'est plus jamais
réécrit.

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

Aucun test ne touche à une production : le hook d'installation est exercé sur
des répertoires temporaires, et le hook d'activation par ses fonctions pures.
