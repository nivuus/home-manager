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
