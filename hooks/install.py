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
   les comptes du broker ; zigbee2mqtt/configuration.yaml le network_key du
   reseau Zigbee ; .env le mot de passe MQTT. Cette phase tourne aussi sur une
   machine deja installee (`install.py --root /`), ou une reecriture
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
