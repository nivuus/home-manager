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
