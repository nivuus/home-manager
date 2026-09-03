# Music Assistant dans `home-manager` — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer `ytube_music_player` par Music Assistant, déployé comme
service du package `home-manager`, relié à Home Assistant, avec cinq plugins.

**Architecture:** Deux conteneurs entrent dans `stack/docker-compose.yml` —
`music-assistant` en `network_mode: host` et son `bgutil-pot-provider` lié à la
boucle locale. Les lecteurs MA proviennent **uniquement** du provider
*Home Assistant Media Players*, et sont renommés `musique_*` pour ne pas
entrer en collision avec les entités Cast sous-jacentes. L'ancienne intégration
n'est retirée qu'après validation sonore, parce que YouTube Music est la seule
source musicale de la maison.

**Tech Stack:** Docker Compose, Home Assistant 2026.8.3 (intégration core
`music_assistant`), Music Assistant server 2.10, websocket HA et websocket MA,
Python 3 + PyYAML pour les tests, Node/rollup pour le wallpanel.

**Spec:** `docs/superpowers/specs/2026-09-03-music-assistant-design.md`

## Global Constraints

- **Trois arbres distincts**, à ne jamais confondre :
  - `REPO` = `/home/mallanic/Projects/Nivuus/packages/home-manager` (versionné)
  - `DEPLOY` = `/opt/nivuus/home-manager` (production, root, **données**)
  - `TOOLS` = `/opt/nivuus/HomeAssistant/data/tools` (sources du wallpanel, hors dépôt)
- **`hooks/install.py --root /` ne doit jamais être lancé sur cette machine** :
  le `.env` de production porte
  `COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml` et la règle 3 du
  hook supprimerait la surcouche de développement en cours d'usage.
- **Tout accès à `DEPLOY` et `TOOLS` passe par `sudo -n`** (root, `drwxr-x---`).
- **Sauvegarde datée avant chaque écriture** dans `DEPLOY/config`, convention du
  dépôt : `<fichier>.backup-music-assistant-20260903`.
- **Tests** : scripts Python autonomes lancés par `make test`. Pas de pytest,
  pas de dépendance hors `python3` + PyYAML.
- **Images** : `ghcr.io/music-assistant/server:latest` et
  `brainicism/bgutil-ytdlp-pot-provider:latest`. Le second suit
  délibérément `latest` — MA installe son client sans version à chaque
  démarrage du fournisseur, épingler le serveur garantirait la dérive.
- **`DEFAULT_PO_TOKEN_SERVER_URL` de MA vaut `http://127.0.0.1:4416`** : ne rien
  saisir dans ce réglage, le défaut correspond au bind.
- **`entity_id` cibles**, fixés à la main, jamais laissés à l'auto-slug :
  `media_player.musique_salon`, `musique_cuisine`, `musique_chambre`,
  `musique_salle_de_bain`, `musique_maison`.
- **Ordre non négociable** : aucun retrait de `ytube_music_player` ni bascule de
  référence avant la validation sonore de la Task 8.
- **Outils validés en amont**, à créer une fois pour toutes en Task 0 :
  `SCRATCH/venv` (avec `websockets`), `SCRATCH/ha.json`, `SCRATCH/hareg.py`.

---

### Task 0 : Outillage websocket

Aucune écriture en production. Produit les outils dont six tâches dépendent.

**Files:**
- Create: `$SCRATCH/ha.json` (identifiants HA, 0600)
- Create: `$SCRATCH/hareg.py` (client websocket HA)
- Create: `$SCRATCH/venv/` (python + `websockets`)

**Interfaces:**
- Produces: `$SCRATCH/venv/bin/python $SCRATCH/hareg.py '<json de messages>'`
  → imprime un tableau JSON des résultats, un par message. Lève `SystemExit`
  au premier échec.

- [ ] **Step 1: Poser la variable et créer le venv**

```bash
export SCRATCH=/tmp/user/0/claude-0/-home-mallanic-Projects-Nivuus-packages-home-manager/32c35198-ec87-4120-a49b-26bc01512a95/scratchpad
python3 -m venv "$SCRATCH/venv"
"$SCRATCH/venv/bin/pip" -q install websockets
"$SCRATCH/venv/bin/python" -c "import websockets; print(websockets.__version__)"
```

Attendu : un numéro de version (17.1 au moment de l'écriture).

- [ ] **Step 2: Extraire les identifiants HA**

Le jeton longue durée existe déjà, utilisé par `TOOLS/haws.py` et
`TOOLS/wallpanel/build.py`. Il est lu dans `.mcp.json`, pas recréé.

```bash
sudo -n python3 -c "
import json, pathlib
c = json.loads(pathlib.Path('/opt/nivuus/HomeAssistant/data/.mcp.json').read_text())['mcpServers']['homeassistant']['env']
pathlib.Path('$SCRATCH/ha.json').write_text(json.dumps({'url': c['HA_URL'], 'token': c['HA_TOKEN']}))
"
sudo -n chown $(id -u):$(id -g) "$SCRATCH/ha.json"
chmod 600 "$SCRATCH/ha.json"
```

- [ ] **Step 3: Écrire le client websocket**

```python
#!/usr/bin/env python3
"""Opérations de registre Home Assistant par websocket (lecture et écriture)."""
import asyncio, json, pathlib, sys
import websockets

CFG = json.loads((pathlib.Path(__file__).parent / "ha.json").read_text())
WS = CFG["url"].replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"

async def call(messages):
    out = []
    async with websockets.connect(WS, max_size=32 * 1024 * 1024) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": CFG["token"]}))
        if json.loads(await ws.recv()).get("type") != "auth_ok":
            raise SystemExit("authentification refusée")
        for i, msg in enumerate(messages, start=1):
            await ws.send(json.dumps({"id": i, **msg}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == i and r.get("type") == "result":
                    if not r.get("success"):
                        raise SystemExit(f"échec {msg['type']} : {r.get('error')}")
                    out.append(r.get("result")); break
    return out

if __name__ == "__main__":
    print(json.dumps(asyncio.run(call(json.loads(sys.argv[1]))), ensure_ascii=False))
```

- [ ] **Step 4: Vérifier l'accès en lecture**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[{"type":"config/entity_registry/list"}]' \
  | python3 -c "import sys,json; print(len([e for e in json.load(sys.stdin)[0] if e['entity_id'].startswith('media_player.')]), 'media_player')"
```

Attendu : `41 media_player`. Un échec d'authentification ici arrête le plan —
tout le reste en dépend.

---

### Task 1 : Les deux services dans la pile

Seule tâche entièrement dans le dépôt, seule tâche commitée à ce stade.

**Files:**
- Modify: `stack/docker-compose.yml` (ajout en fin de fichier)
- Test: `tests/test_compose_portable.py`

**Interfaces:**
- Produces: services `music-assistant` et `bgutil-pot-provider` dans
  `main["services"]`, consommés par la Task 2.

- [ ] **Step 1: Écrire le test qui échoue**

Dans `tests/test_compose_portable.py`, remplacer la constante `SERVICES` :

```python
SERVICES = ("homeassistant", "docker-socket-proxy", "mosquitto",
            "zigbee2mqtt", "otbr", "matterjs-server",
            "music-assistant", "bgutil-pot-provider")
```

Ajouter `("music-assistant", "./music_assistant:/data")` au tuple de la
boucle « Les donnees sont montees en relatif », puis, juste après le bloc du
proxy Docker :

```python
# Le generateur de jetons PO n'ecoute que sur la boucle locale. Expose au LAN,
# il offrirait a quiconque un generateur de jetons YouTube tournant sous
# l'identite de la maison.
check("po token: ecoute limitee a la boucle locale",
      main["services"]["bgutil-pot-provider"]["ports"],
      ["127.0.0.1:4416:4416"])

# MA sert ses flux aux enceintes depuis le port 8097 : en reseau bridge il leur
# annoncerait une adresse qu'elles ne savent pas joindre.
check("music-assistant: reseau de l'hote",
      main["services"]["music-assistant"].get("network_mode"), "host")
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 tests/test_compose_portable.py`
Expected: FAIL — `les six services sont declares: got [...], want [...]` puis
un `KeyError: 'bgutil-pot-provider'`.

- [ ] **Step 3: Ajouter les deux services**

À la fin de `stack/docker-compose.yml` :

```yaml
  # Music Assistant. Il appartient a ce package pour la meme raison que
  # mosquitto : c'est un service dont Home Assistant depend, et le laisser au
  # docker_marketplace de HA reintroduirait la dependance circulaire que ce
  # package existe pour supprimer.
  #
  # network_mode: host n'est pas decoratif. MA sert ses flux audio aux
  # enceintes depuis le port 8097 ; en reseau bridge il leur annoncerait une
  # adresse de conteneur qu'elles ne savent pas joindre, et la lecture
  # echouerait sans message clair.
  music-assistant:
    container_name: music-assistant
    image: ghcr.io/music-assistant/server:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./music_assistant:/data
      - /etc/localtime:/etc/localtime:ro
    environment:
      LOG_LEVEL: info

  # Generateur de jetons « Proof of Origin » exige par YouTube. MA verifie que
  # cette URL repond au demarrage du fournisseur et leve LoginFailed sinon :
  # le service n'est pas optionnel.
  #
  # Publie sur la SEULE boucle locale, comme docker-socket-proxy : c'est un
  # rouage interne de MA. Le --host 0.0.0.0 ne concerne que l'interieur du
  # conteneur, sans quoi la publication ne joindrait rien.
  #
  # Le tag suit `latest` DELIBEREMENT, et c'est l'inverse du reflexe. MA
  # installe le client bgutil-ytdlp-pot-provider SANS VERSION a chaque
  # demarrage du fournisseur (« Google breaks things quite often », dit son
  # code) : le client suit donc latest, et epingler le serveur garantirait la
  # derive entre les deux, que bgutil refuse. C'est le pin qui casse.
  bgutil-pot-provider:
    container_name: bgutil-pot-provider
    image: brainicism/bgutil-ytdlp-pot-provider:latest
    restart: unless-stopped
    init: true
    ports:
      - "127.0.0.1:4416:4416"
    command: ["--host", "0.0.0.0"]
```

- [ ] **Step 4: Lancer la suite complète**

Run: `make test`
Expected: les cinq suites passent, dont `test_compose_portable: OK`.

- [ ] **Step 5: Commit**

```bash
git add stack/docker-compose.yml tests/test_compose_portable.py
git commit -m "feat(stack): Music Assistant et son generateur de jetons PO

Le PO token server n'ecoute que sur la boucle locale, comme le proxy
Docker. Son tag suit latest a dessein : MA installe le client sans
version a chaque demarrage, epingler le serveur ferait deriver les deux.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ubH53NdfL4FrJnqGmTvnG"
```

---

### Task 2 : Dépose et démarrage sur le serveur

**Files:**
- Modify: `/opt/nivuus/home-manager/docker-compose.yml` (copie depuis le dépôt)

**Interfaces:**
- Consumes: le compose de la Task 1.
- Produces: MA joignable sur `http://127.0.0.1:8095`, PO provider sur
  `http://127.0.0.1:4416/ping`.

- [ ] **Step 1: Sauvegarder le compose de production**

```bash
sudo -n cp -a /opt/nivuus/home-manager/docker-compose.yml \
  /opt/nivuus/home-manager/docker-compose.yml.backup-music-assistant-20260903
```

- [ ] **Step 2: Déposer le nouveau compose**

`install.py` n'est **pas** utilisé (voir Global Constraints).

```bash
sudo -n cp /home/mallanic/Projects/Nivuus/packages/home-manager/stack/docker-compose.yml \
  /opt/nivuus/home-manager/docker-compose.yml
sudo -n grep -c "" /opt/nivuus/home-manager/docker-compose.yml
```

- [ ] **Step 3: Vérifier que la pile reste cohérente avant de démarrer quoi que ce soit**

```bash
cd /opt/nivuus/home-manager && sudo -n docker compose config --services | sort
```

Expected : les 8 services, `bgutil-pot-provider` et `music-assistant` inclus.
Le `.env` porte encore la surcouche de dev : si cette commande échoue sur un
fichier manquant, **arrêter** et vérifier que `docker-compose.dev.yml` est
toujours présent dans `DEPLOY`.

- [ ] **Step 4: Démarrer les deux nouveaux services seulement**

```bash
cd /opt/nivuus/home-manager && sudo -n docker compose up -d music-assistant bgutil-pot-provider
sudo -n docker ps --filter name=music-assistant --filter name=bgutil --format '{{.Names}}\t{{.Status}}'
```

- [ ] **Step 5: Vérifier les deux points d'entrée**

```bash
curl -s -o /dev/null -w "MA 8095 : HTTP %{http_code}\n" http://127.0.0.1:8095/
curl -s -o /dev/null -w "PO 4416 : HTTP %{http_code}\n" http://127.0.0.1:4416/ping
```

Expected : `HTTP 200` pour les deux.

- [ ] **Step 6: Vérifier que le PO provider n'est PAS joignable depuis le LAN**

```bash
IP=$(ip -4 addr show scope global | grep -oP '(?<=inet )\d+\.\d+\.\d+\.\d+' | head -1)
curl -s -m 3 -o /dev/null -w "depuis $IP : %{http_code}\n" "http://$IP:4416/ping" || echo "refuse — attendu"
```

Expected : refus de connexion, ou `000`. Une réponse `200` ici est une
régression de sécurité : revenir à la Task 1.

- [ ] **Step 7: Lire les journaux de MA**

```bash
sudo -n docker logs music-assistant 2>&1 | tail -30
```

Expected : démarrage sans traceback. Les avertissements sur les providers
absents sont normaux — rien n'est encore configuré.

---

### Task 3 : Ménage du registre d'entités

Vérifié en amont : les entités supprimées ici ne sont référencées **nulle
part** hors du registre — ni dans `automations.yaml`, `scripts.yaml`,
`configuration.yaml`, `scenes.yaml`, `wallpanel.jinja`, `wallpanel.js`, les
trois dashboards, `homeassistant.exposed_entities`, `config/packages/`, ni par
`device_id`.

**Files:**
- Modify: registre d'entités de HA (par websocket)

**Interfaces:**
- Consumes: `$SCRATCH/hareg.py` de la Task 0.
- Produces: `media_player.bar_de_son` et `media_player.enceinte_chambre`,
  consommés par les Tasks 4 (Step 3) et 6.

- [ ] **Step 1: Sauvegarder le registre**

```bash
sudo -n cp -a /opt/nivuus/home-manager/config/.storage/core.entity_registry \
  /opt/nivuus/home-manager/config/.storage/core.entity_registry.backup-music-assistant-20260903
```

- [ ] **Step 2: Supprimer les trois enceintes mortes**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[
 {"type":"config/entity_registry/remove","entity_id":"media_player.enceinte_bureau"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.enceinte_bureau_2"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.enceinte_chambre"}
]'
```

`enceinte_bureau_2` est un **groupe Cast**, pas une enceinte — c'est bien une
suppression d'entité, pas un débranchement de matériel.

- [ ] **Step 3: Supprimer les 11 Play-Fi fantômes**

`2379390371` est **absente de cette liste** : c'est la vraie barre de son,
UUID `25992067-183e-44d4-f36e-df1b7f580aec`, vivante à 192.168.0.142.

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device2379391336"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device2379394027"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device21e9582235"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device2498ee6876"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device26213d0835"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device26213d9805"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device2498ee6060"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device2498ee1161"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device26213d0723"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device26213d7518"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.playfi2device2498ee0317"}
]'
```

- [ ] **Step 4: Réactiver et renommer la barre de son**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[
 {"type":"config/entity_registry/update",
  "entity_id":"media_player.playfi2device2379390371",
  "disabled_by":null,
  "name":"Bar de son",
  "new_entity_id":"media_player.bar_de_son"}
]'
```

- [ ] **Step 5: Renommer l'enceinte de la chambre**

`media_player.bureau` est physiquement l'enceinte de la **chambre** (device
« Enceinte Chambre », Google Home Mini, 192.168.0.202). Le renommage n'est
possible qu'après le Step 2, qui libère le nom.

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[
 {"type":"config/entity_registry/update",
  "entity_id":"media_player.bureau",
  "new_entity_id":"media_player.enceinte_chambre"}
]'
```

- [ ] **Step 6: Vérifier l'état final**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[{"type":"config/entity_registry/list"}]' \
  | python3 -c "
import sys, json
vus = {e['entity_id']: e.get('disabled_by') for e in json.load(sys.stdin)[0]}
partis = ['media_player.enceinte_bureau','media_player.enceinte_bureau_2']
for e in partis:
    print(f'{e:42} present={e in vus}   (attendu False)')
for e in ['media_player.bar_de_son','media_player.enceinte_chambre']:
    print(f'{e:42} present={e in vus}   disabled={vus.get(e)}   (attendu True / None)')
print('playfi restantes :', [k for k in vus if 'playfi' in k], '(attendu [])')
"
```

Cette tâche précède volontairement toute configuration de Music Assistant :
`media_player.bar_de_son` et `media_player.enceinte_chambre` doivent exister
**avant** que la Task 4 ne les donne à MA, sinon le provider est bâti sur des
noms qui changeront sous lui.

---

### Task 4 : Plugin Home Assistant et provider *HA Media Players*

Le plugin est **prérequis** de tout le reste : il apporte le provider de
lecteurs **et** les moteurs IA/TTS que réclame AI Radio.

**Files:** aucun fichier du dépôt. Configuration stockée dans
`/opt/nivuus/home-manager/music_assistant/`.

**Interfaces:**
- Consumes: MA joignable (Task 2).
- Produces: les 4 lecteurs HA visibles dans MA, nommés à la Task 6.

- [ ] **Step 1: Ouvrir l'interface de MA**

`http://127.0.0.1:8095` — ou l'adresse LAN du serveur depuis un poste.
Premier lancement : MA demande de créer le compte administrateur local.

- [ ] **Step 2: Ajouter le plugin Home Assistant**

Paramètres → Plugins → Ajouter → **Home Assistant**.
URL demandée : `https://home.allanic.me`.

**Étape opérateur** : MA ouvre la page d'autorisation de Home Assistant. Elle
demande une connexion dans le navigateur — c'est la première des trois
étapes qui ne peuvent pas être automatisées.

- [ ] **Step 3: Ajouter le provider Home Assistant Media Players**

Paramètres → Fournisseurs → Ajouter → **Home Assistant Media Players**.
Cocher exactement ces quatre entités, et aucune autre :

```
media_player.enceinte_cuisine
media_player.bureau
media_player.google_home_salle_de_bain
media_player.bar_de_son
```

Les quatre existent et sont actives : la Task 3 a supprimé les enceintes
mortes, réactivé la barre de son et renommé l'enceinte de la chambre.

Ne cocher **ni** `media_player.maison` (groupe Cast cassé), **ni** les
téléviseurs, **ni** les tablettes.

- [ ] **Step 4: Vérifier que MA voit les lecteurs**

```bash
sudo -n docker logs music-assistant 2>&1 | grep -i "player" | tail -20
```

Expected : les lecteurs HA apparaissent. Les voir aussi dans l'onglet
*Lecteurs* de l'UI.

---

### Task 5 : Fournisseur YouTube Music

**Files:** aucun fichier du dépôt.

**Interfaces:**
- Consumes: PO provider joignable (Task 2).
- Produces: une bibliothèque musicale dans MA, consommée par la Task 8.

- [ ] **Step 1: Extraire le cookie**

**Étape opérateur**, la deuxième des trois. Dans une fenêtre de navigation
**privée** : se connecter à `https://music.youtube.com`, ouvrir les outils de
développement, onglet Réseau, recharger, prendre une requête vers
`music.youtube.com`, et copier **la valeur entière de l'en-tête `Cookie`** —
elle doit contenir `__Secure-3PAPISID`.

La navigation privée n'est pas un détail : un cookie de session ordinaire est
invalidé dès que le navigateur se déconnecte ou renouvelle sa session.

- [ ] **Step 2: Ajouter le fournisseur**

Paramètres → Fournisseurs → Ajouter → **YouTube Music**.

| Champ | Valeur |
|---|---|
| Username | l'adresse Gmail du compte Premium |
| Cookie | la valeur copiée au Step 1 |
| PO Token server URL | **laisser vide** — le défaut `http://127.0.0.1:4416` correspond au bind |

- [ ] **Step 3: Vérifier que la configuration passe**

```bash
sudo -n docker logs music-assistant 2>&1 | tail -40
```

Expected : pas de `LoginFailed`. Deux échecs à distinguer :

- « PO Token server URL is not reachable » → le conteneur
  `bgutil-pot-provider` ne répond pas, revenir à la Task 2 Step 5 ;
- une erreur d'authentification → le cookie est mauvais ou expiré, reprendre
  le Step 1 dans une fenêtre privée neuve.

- [ ] **Step 4: Vérifier la bibliothèque**

Dans l'UI, onglet *Musique* : les playlists du compte apparaissent, dont
« Mon supermix ». Noter son URI MA — elle sert à la Task 10.

- [ ] **Step 5: Régler la langue et le mélange**

Reprise de l'ancienne configuration : langue **`fr`**, lecture aléatoire
activée par défaut sur les files, limite de file **25**.

---

### Task 6 : Les quatre lecteurs et le groupe

**Files:** aucun fichier du dépôt.

**Interfaces:**
- Consumes: le provider de la Task 4.
- Produces: 5 lecteurs MA, dont le groupe, consommés par la Task 7.

- [ ] **Step 1: Renommer les quatre lecteurs dans MA**

Paramètres → Lecteurs. Le nom MA commande l'`entity_id` que créera
l'intégration côté HA : le préfixe `Musique` est ce qui empêche la collision
avec les entités Cast sous-jacentes, qui gardent leurs noms.

| Lecteur (entité HA sous-jacente) | Nom MA |
|---|---|
| `media_player.bar_de_son` | `Musique Salon` |
| `media_player.enceinte_cuisine` | `Musique Cuisine` |
| `media_player.bureau` | `Musique Chambre` |
| `media_player.google_home_salle_de_bain` | `Musique Salle de bain` |

- [ ] **Step 2: Créer le groupe**

Paramètres → Lecteurs → Créer un groupe. Nom : **`Musique Maison`**.
Membres : les quatre lecteurs ci-dessus.

Ce groupe remplace le groupe Cast `media_player.maison`, cassé depuis que les
enceintes du bureau ont disparu. La synchronisation est celle de MA sur quatre
flux HTTP séparés, conséquence assumée de la contrainte « uniquement les
providers HA » : un léger décalage entre pièces est possible.

- [ ] **Step 3: Vérifier**

L'onglet *Lecteurs* montre cinq entrées : les quatre pièces et `Musique Maison`.

---

### Task 7 : Intégration `music_assistant` côté Home Assistant

**Files:**
- Modify: registre d'entités de HA (par websocket)

**Interfaces:**
- Consumes: les 5 lecteurs de la Task 6, `$SCRATCH/hareg.py` de la Task 0.
- Produces: les `entity_id` `media_player.musique_*`, consommés par les
  Tasks 10 à 12.

- [ ] **Step 1: Ajouter l'intégration**

Dans HA : Paramètres → Appareils et services → Ajouter une intégration →
**Music Assistant**. L'intégration est **core** depuis HA 2024.12 — ne pas
passer par HACS. Elle découvre le serveur local ; sinon saisir
`http://127.0.0.1:8095`.

- [ ] **Step 2: Relever les `entity_id` réellement créés**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[{"type":"config/entity_registry/list"}]' \
  | python3 -c "
import sys, json
for e in json.load(sys.stdin)[0]:
    if e.get('platform') == 'music_assistant':
        print(e['entity_id'], '|', e.get('original_name'), '| id =', e['id'])
"
```

C'est ici que se joue le piège documenté dans `configuration.yaml` : si un
`entity_id` porte un suffixe `_2`, il **doit** être corrigé au Step 3, sinon
la bascule des Tasks 10 à 12 pointera dans le vide.

- [ ] **Step 3: Fixer les cinq `entity_id`**

Remplacer les `<id>` par les valeurs relevées au Step 2.

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[
 {"type":"config/entity_registry/update","entity_id":"<actuel salon>","new_entity_id":"media_player.musique_salon"},
 {"type":"config/entity_registry/update","entity_id":"<actuel cuisine>","new_entity_id":"media_player.musique_cuisine"},
 {"type":"config/entity_registry/update","entity_id":"<actuel chambre>","new_entity_id":"media_player.musique_chambre"},
 {"type":"config/entity_registry/update","entity_id":"<actuel salle de bain>","new_entity_id":"media_player.musique_salle_de_bain"},
 {"type":"config/entity_registry/update","entity_id":"<actuel maison>","new_entity_id":"media_player.musique_maison"}
]'
```

- [ ] **Step 4: Vérifier les cinq entités**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[{"type":"config/entity_registry/list"}]' \
  | python3 -c "
import sys, json
cible = {'media_player.musique_salon','media_player.musique_cuisine',
         'media_player.musique_chambre','media_player.musique_salle_de_bain',
         'media_player.musique_maison'}
vus = {e['entity_id'] for e in json.load(sys.stdin)[0]}
manque = cible - vus
print('manquantes :', sorted(manque) if manque else 'aucune')
"
```

Expected : `manquantes : aucune`.

---

### Task 8 : Point de validation sonore

**Aucune tâche suivante ne peut commencer avant que cette tâche passe.**
YouTube Music est la seule source musicale de la maison : tout ce qui suit
démonte l'ancienne installation.

**Files:** aucun.

- [ ] **Step 1: Jouer sur chaque pièce**

Depuis l'UI de MA, lancer une piste sur `Musique Cuisine`, puis
`Musique Chambre`, puis `Musique Salle de bain`, puis `Musique Salon`.

Vérifier **à l'oreille** que le son sort de la bonne enceinte. Une entité
`bar_de_son` qui répond sans qu'aucun son ne sorte est le symptôme d'une
Play-Fi fantôme : vérifier que c'est bien l'UUID `25992067-…` qui a été
réactivé à la Task 3.

- [ ] **Step 2: Jouer sur le groupe**

Lancer sur `Musique Maison` et vérifier que les quatre enceintes sonnent.

- [ ] **Step 3: Vérifier depuis Home Assistant**

```bash
TOK=$(python3 -c "import json;print(json.load(open('$SCRATCH/ha.json'))['token'])")
for e in musique_salon musique_cuisine musique_chambre musique_salle_de_bain musique_maison; do
  curl -s -H "Authorization: Bearer $TOK" "https://home.allanic.me/api/states/media_player.$e" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['entity_id']:38} {d['state']}\")"
done
```

Expected : cinq entités, aucune en `unavailable`.

- [ ] **Step 4: Porte**

Si l'un des trois steps échoue, **s'arrêter ici**. L'ancienne intégration est
encore en place et la maison a toujours sa musique. Diagnostiquer avant de
poursuivre.

---

### Task 9 : Les quatre plugins restants

**Files:** aucun fichier du dépôt.

**Interfaces:**
- Consumes: le plugin Home Assistant de la Task 4 (moteurs IA et TTS).

- [ ] **Step 1: AI Radio**

Paramètres → Plugins → Ajouter → **AI Radio**.

| Réglage | Valeur |
|---|---|
| Moteur IA | `conversation.gemini_flash` (via le plugin Home Assistant) |
| Moteur TTS | `tts.google_ai_tts` |
| Ville / pays | Paris, France — pour les bulletins météo |

Le plugin refuse de dépasser son premier écran sans un moteur IA **et** un
moteur TTS : si les listes sont vides, le plugin Home Assistant de la Task 4
n'est pas correctement connecté.

- [ ] **Step 2: Party**

Paramètres → Plugins → Ajouter → **Party**. Lecteur : `Musique Maison`.
Une instance par lecteur est possible ; une seule sur le groupe suffit.

- [ ] **Step 3: Music Quiz**

Paramètres → Plugins → Ajouter → **Music Quiz**. Lecteur : `Musique Maison`.

- [ ] **Step 4: LastFM Scrobbler**

Paramètres → Plugins → Ajouter → **LastFM Scrobbler**. Choisir **Last.FM**
(et non LibreFM), puis l'utilisateur MA à scrobbler.

**Étape opérateur**, la troisième et dernière : l'autorisation OAuth Last.fm
se fait dans un navigateur.

- [ ] **Step 5: Vérifier**

```bash
sudo -n docker logs music-assistant 2>&1 | tail -40
```

Expected : les cinq plugins chargés, aucun traceback.

---

### Task 10 : Retrait de `ytube_music_player` et bascule des trois fichiers YAML

**Files:**
- Modify: `/opt/nivuus/home-manager/config/scripts.yaml:968-984`
- Modify: `/opt/nivuus/home-manager/config/configuration.yaml:219-241`
- Modify: `/opt/nivuus/home-manager/config/custom_templates/wallpanel.jinja:116-120`
- Delete: `/opt/nivuus/home-manager/config/custom_components/ytube_music_player/`
- Delete: `/opt/nivuus/home-manager/config/.storage/header_ytube_music_player.json`

**Interfaces:**
- Consumes: `media_player.musique_maison` et `musique_*` de la Task 7.

- [ ] **Step 1: Sauvegarder les trois fichiers**

```bash
C=/opt/nivuus/home-manager/config
for f in scripts.yaml configuration.yaml custom_templates/wallpanel.jinja; do
  sudo -n cp -a "$C/$f" "$C/$f.backup-music-assistant-20260903"
done
```

- [ ] **Step 2: Retirer l'entrée de configuration**

Dans HA : Paramètres → Appareils et services → **yTubeMusic** → Supprimer.
Puis dans HACS : dépôt `ytube_music_player` → Supprimer.

- [ ] **Step 3: Basculer le script du Supermix**

Dans `scripts.yaml`, remplacer le corps de `lancer_supermix_maison`. Le
`select_source` disparaît : il servait à choisir l'enceinte, ce que le lecteur
de groupe fait désormais par construction.

```yaml
lancer_supermix_maison:
  alias: Musique - Lancer Mon Supermix (Maison)
  icon: mdi:music-circle
  description: Lance la playlist « Mon supermix » de YouTube Music sur le groupe Musique Maison
  mode: single
  sequence:
  - target:
      entity_id: media_player.musique_maison
    data:
      media_id: PLl-J6k2oaq_ef7_jQ8xXFH4KJQpXmY_A7
      media_type: playlist
    action: music_assistant.play_media
```

Si l'URI relevée à la Task 5 Step 4 diffère de l'identifiant brut, utiliser
celle-là : MA suffixe les identifiants de playlist YouTube d'un délimiteur
(`YT_PLAYLIST_ID_DELIMITER`) parce qu'ils ne sont pas uniques entre comptes.

- [ ] **Step 4: Basculer le déclencheur du capteur *Wallpanel héros***

Dans `configuration.yaml`, dans la liste `entity_id` du `trigger` (vers la
ligne 222), remplacer les deux premières lignes :

```yaml
        entity_id:
          - media_player.musique_salon
          - media_player.musique_cuisine
          - media_player.musique_chambre
          - media_player.musique_maison
          - media_player.televiseur_salon_3
```

(`media_player.ytube_music_player` et `media_player.maison` sortent, les
quatre `musique_*` entrent ; le reste de la liste est inchangé.)

- [ ] **Step 5: Basculer la macro `media_en_cours()`**

Dans `custom_templates/wallpanel.jinja`, lignes 116-120 :

```jinja
{%- macro media_en_cours() -%}
  {{- (states('media_player.musique_salon') in ['playing','paused','buffering']
       or states('media_player.musique_cuisine') in ['playing','paused','buffering']
       or states('media_player.musique_chambre') in ['playing','paused','buffering']
       or states('media_player.musique_maison') in ['playing','paused','buffering','on']
       or states('media_player.televiseur_salon_3') in ['playing','paused','buffering','on']) | lower -}}
{%- endmacro -%}
```

- [ ] **Step 6: Supprimer le code et le jeton de l'intégration**

```bash
C=/opt/nivuus/home-manager/config
sudo -n rm -rf "$C/custom_components/ytube_music_player"
sudo -n rm -f "$C/.storage/header_ytube_music_player.json"
```

- [ ] **Step 7: Vérifier la configuration puis recharger**

```bash
sudo -n docker exec homeassistant hass --script check_config -c /config 2>&1 | tail -20
```

Expected : `Testing configuration at /config` puis aucune erreur.

```bash
TOK=$(python3 -c "import json;print(json.load(open('$SCRATCH/ha.json'))['token'])")
curl -s -X POST -H "Authorization: Bearer $TOK" https://home.allanic.me/api/services/script/reload
curl -s -X POST -H "Authorization: Bearer $TOK" https://home.allanic.me/api/services/template/reload
```

- [ ] **Step 8: Vérifier que la macro rend bien**

```bash
TOK=$(python3 -c "import json;print(json.load(open('$SCRATCH/ha.json'))['token'])")
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"template":"{% from '"'"'wallpanel.jinja'"'"' import media_en_cours %}{{ media_en_cours() }}"}' \
  https://home.allanic.me/api/template
```

Expected : `true` ou `false`, **pas** une erreur de template.

- [ ] **Step 9: Vérifier qu'il ne reste aucune référence vivante**

```bash
C=/opt/nivuus/home-manager/config
for f in scripts.yaml configuration.yaml scenes.yaml automations.yaml custom_templates/wallpanel.jinja; do
  echo -n "$f : "; sudo -n grep -c "ytube" "$C/$f" 2>/dev/null || echo 0
done
```

Expected : `0` partout.

---

### Task 11 : Bascule du wallpanel (application TypeScript)

`www/wallpanel/wallpanel.js` est **généré**. Le modifier sans modifier
`pieces.ts` se ferait écraser au prochain build.

**Files:**
- Modify: `/opt/nivuus/HomeAssistant/data/tools/wallpanel-app/src/pieces.ts` (3 blocs `sources`)
- Regenerate: `/opt/nivuus/home-manager/config/www/wallpanel/wallpanel.js`

**Interfaces:**
- Consumes: `media_player.musique_*` de la Task 7.

- [ ] **Step 1: Sauvegarder**

```bash
sudo -n cp -a /opt/nivuus/HomeAssistant/data/tools/wallpanel-app/src/pieces.ts \
  /opt/nivuus/HomeAssistant/data/tools/wallpanel-app/src/pieces.ts.backup-music-assistant-20260903
sudo -n cp -a /opt/nivuus/home-manager/config/www/wallpanel \
  /opt/nivuus/home-manager/config/www/wallpanel.backup-music-assistant-20260903
```

- [ ] **Step 2: Salon — remplacer le bloc `sources` (vers la ligne 211)**

```ts
    sources: [
      {
        nom: 'Musique',
        titre: ['media_player.musique_salon'],
        sousTitre: ['media_player.musique_salon'],
        affiche: ['media_player.musique_salon'],
        progression: ['media_player.musique_salon'],
        transport: ['media_player.musique_salon'],
        volume: ['media_player.musique_salon'],
      },
      {
        nom: 'Multiroom',
        titre: ['media_player.musique_maison'],
        sousTitre: ['media_player.musique_maison'],
        affiche: ['media_player.musique_maison'],
        progression: ['media_player.musique_maison'],
        transport: ['media_player.musique_maison'],
        volume: ['media_player.musique_maison'],
      },
```

- [ ] **Step 3: Bureau — remplacer le bloc `sources` (vers la ligne 315)**

Le bureau n'a plus d'enceinte : sa source « Musique » commande la barre de son
du salon, et gagne le « Multiroom » qu'il n'avait pas.

```ts
    sources: [
      {
        nom: 'Musique',
        titre: ['media_player.musique_salon'],
        sousTitre: ['media_player.musique_salon'],
        affiche: ['media_player.musique_salon'],
        progression: ['media_player.musique_salon'],
        transport: ['media_player.musique_salon'],
        volume: ['media_player.musique_salon'],
      },
      {
        nom: 'Multiroom',
        titre: ['media_player.musique_maison'],
        sousTitre: ['media_player.musique_maison'],
        affiche: ['media_player.musique_maison'],
        progression: ['media_player.musique_maison'],
        transport: ['media_player.musique_maison'],
        volume: ['media_player.musique_maison'],
      },
    ],
```

- [ ] **Step 4: Cuisine — remplacer le bloc `sources` (vers la ligne 407)**

```ts
    sources: [
      {
        nom: 'Musique',
        titre: ['media_player.musique_cuisine'],
        sousTitre: ['media_player.musique_cuisine'],
        affiche: ['media_player.musique_cuisine'],
        progression: ['media_player.musique_cuisine'],
        transport: ['media_player.musique_cuisine'],
        volume: ['media_player.musique_cuisine'],
      },
      {
        nom: 'Multiroom',
        titre: ['media_player.musique_maison'],
        sousTitre: ['media_player.musique_maison'],
        affiche: ['media_player.musique_maison'],
        progression: ['media_player.musique_maison'],
        transport: ['media_player.musique_maison'],
        volume: ['media_player.musique_maison'],
      },
    ],
```

- [ ] **Step 5: Lancer les tests de l'application**

```bash
cd /opt/nivuus/HomeAssistant/data/tools/wallpanel-app && sudo -n npm run test
```

Expected : la suite `vitest` passe. Un test qui référence
`media_player.ytube_music_player` doit être mis à jour vers `musique_salon`.

- [ ] **Step 6: Reconstruire**

```bash
cd /opt/nivuus/HomeAssistant/data/tools/wallpanel-app && sudo -n npm run build
```

- [ ] **Step 7: Vérifier que le généré est propre**

```bash
echo -n "ytube dans le bundle : "
sudo -n grep -c "ytube" /opt/nivuus/home-manager/config/www/wallpanel/wallpanel.js || echo 0
echo -n "musique_ dans le bundle : "
sudo -n grep -o "musique_[a-z_]*" /opt/nivuus/home-manager/config/www/wallpanel/wallpanel.js | sort -u
```

Expected : `0` pour `ytube` ; `musique_salon`, `musique_cuisine`,
`musique_maison` présents.

---

### Task 12 : Bascule des trois dashboards Lovelace

`.storage/lovelace.wallpanel_*` est **généré** par `TOOLS/wallpanel/rooms.py`
puis déployé par `build.py --deploy`.

**Files:**
- Modify: `/opt/nivuus/HomeAssistant/data/tools/wallpanel/rooms.py:8-53`
- Regenerate: `TOOLS/wallpanel/genere/{salon,bureau,cuisine}.yaml`
- Deploy: `.storage/lovelace.wallpanel_{salon,bureau,cuisine}`

**Interfaces:**
- Consumes: `media_player.musique_*` de la Task 7.

- [ ] **Step 1: Sauvegarder**

```bash
sudo -n cp -a /opt/nivuus/HomeAssistant/data/tools/wallpanel/rooms.py \
  /opt/nivuus/HomeAssistant/data/tools/wallpanel/rooms.py.backup-music-assistant-20260903
C=/opt/nivuus/home-manager/config/.storage
for p in salon bureau cuisine; do
  sudo -n cp -a "$C/lovelace.wallpanel_$p" "$C/lovelace.wallpanel_$p.backup-music-assistant-20260903"
done
```

- [ ] **Step 2: Retirer le sélecteur de source de `_lecteur_media`**

Ligne 29 de `rooms.py`, dans le dict rendu par `_lecteur_media` : supprimer la
ligne `"source": "icon",`. Elle affichait le sélecteur d'enceinte de
`ytube_music_player`, que MA n'a pas — un lecteur MA par pièce le remplace.

- [ ] **Step 3: Basculer `_MEDIA` (lignes 41-53)**

L'exclusivité entre cartes est conservée : musique de pièce > multiroom > TV.

```python
_MEDIA = {
    "type": "vertical-stack",
    "cards": [
        _lecteur_media("media_player.musique_salon", "Salon", "default",
                       ["playing", "paused", "buffering"], []),
        _lecteur_media("media_player.musique_maison", "Maison", "default",
                       ["playing", "paused", "buffering", "on"],
                       ["media_player.musique_salon"]),
        _lecteur_media("media_player.televiseur_salon_3", "Télévision", "full-cover-fit",
                       ["playing", "paused", "buffering", "on"],
                       ["media_player.musique_salon", "media_player.musique_maison"]),
    ],
}
```

Mettre également à jour le commentaire au-dessus, qui parle encore de « YTM »
et de « Supermix (YTM) », ainsi que la docstring de `_lecteur_media` dont la
dernière ligne dit `YTM > media_player.maison > télévision` — elle devient
`musique de pièce > musique_maison > télévision`.

- [ ] **Step 4: Générer sans déployer**

```bash
cd /opt/nivuus/HomeAssistant/data && sudo -n python3 tools/wallpanel/build.py
echo -n "ytube dans les generes : "
sudo -n grep -rc "ytube" tools/wallpanel/genere/ | grep -v ":0" || echo "aucun"
```

Expected : `aucun`.

- [ ] **Step 5: Déployer les trois dashboards**

`build.py --deploy` valide que **toute** entité utilisée existe réellement dans
HA avant d'écrire, et abandonne sinon. C'est le filet de sécurité de cette
tâche : un `musique_*` mal nommé à la Task 7 fait échouer ici, pas en silence.

```bash
cd /opt/nivuus/HomeAssistant/data && sudo -n python3 tools/wallpanel/build.py --deploy salon bureau cuisine
```

Expected : trois lignes de génération, aucun « DÉPLOIEMENT ABANDONNÉ ».

- [ ] **Step 6: Vérifier les dashboards déployés**

```bash
C=/opt/nivuus/home-manager/config/.storage
for p in salon bureau cuisine; do
  echo -n "$p — ytube : $(sudo -n grep -oc 'ytube' "$C/lovelace.wallpanel_$p" 2>/dev/null || echo 0)"
  echo "  musique_ : $(sudo -n grep -o 'musique_[a-z_]*' "$C/lovelace.wallpanel_$p" | sort -u | tr '\n' ' ')"
done
```

Expected : `ytube : 0` sur les trois.

- [ ] **Step 7: Vérifier à l'œil sur une tablette**

Recharger la tablette du salon. Lancer une piste sur `Musique Salon` : la carte
média doit apparaître, afficher la pochette, et ses commandes doivent agir.

---

### Task 13 : Documentation et clôture

**Files:**
- Modify: `README.md` (tableau des services)
- Modify: `CLAUDE.md` (section « Décisions à ne pas défaire »)

- [ ] **Step 1: Compléter le tableau du README**

Sous `matterjs-server`, ajouter :

```markdown
| `music-assistant` | serveur musical, sur le port 8095 |
| `bgutil-pot-provider` | générateur de jetons YouTube, boucle locale seule |
```

Et corriger la phrase d'introduction : « Home Assistant et les **cinq**
services dont il dépend » devient « et les **sept** services dont il dépend ».

- [ ] **Step 2: Ajouter les trois décisions à `CLAUDE.md`**

Dans « Décisions à ne pas défaire », après le paragraphe sur mosquitto :

```markdown
- **Music Assistant appartient à ce package**, pour la raison qui a fait
  reprendre mosquitto : c'est un service dont Home Assistant dépend. Il
  remplace `ytube_music_player` depuis le 2026-09-03.
- **Le tag de `bgutil-pot-provider` suit `latest`, à dessein.** Épingler
  serait le réflexe et serait l'erreur : MA installe le client
  `bgutil-ytdlp-pot-provider` **sans version** à chaque démarrage du
  fournisseur YouTube Music (« Google breaks things quite often », dit son
  code), et bgutil exige que client et serveur s'accordent. Épingler le
  serveur garantit donc la dérive. Le service n'est pas optionnel : MA teste
  son URL au démarrage et lève `LoginFailed` si elle ne répond pas.
- **Les lecteurs Music Assistant sont préfixés `musique_`.** Ils sont bâtis
  sur des entités Cast qui existent toujours ; sans préfixe explicite,
  l'intégration `music_assistant` créerait des `media_player.enceinte_cuisine_2`
  imprévisibles — le même piège que les héros du wallpanel, documenté dans
  `config/configuration.yaml`.
```

- [ ] **Step 3: Lancer la suite complète**

```bash
make test NIVUUS_INSTALLER_DIR=$HOME/Projects/Nivuus/packages/installer
```

Expected : les cinq suites passent.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md docs/superpowers/plans/2026-09-03-music-assistant.md
git commit -m "docs(music-assistant): les deux services et leurs trois pieges

Le tag latest du generateur de jetons PO est une decision, pas un oubli :
MA installe son client sans version a chaque demarrage, et bgutil exige
que les deux s'accordent.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ubH53NdfL4FrJnqGmTvnG"
```

---

## Ce que ce plan laisse à l'opérateur

Trois étapes, toutes dans un navigateur, aucune automatisable :

| # | Task | Étape |
|---|---|---|
| 1 | Task 4 Step 2 | autorisation du plugin Home Assistant dans MA |
| 2 | Task 5 Step 1 | extraction du cookie YouTube Music en navigation privée |
| 3 | Task 9 Step 4 | autorisation OAuth Last.fm |

## Ordre des dépendances

```
Task 0  outillage websocket
  └─ Task 1  compose (dépôt, commité)
       └─ Task 2  dépose et démarrage
            └─ Task 3  ménage du registre — crée bar_de_son, enceinte_chambre
                 ├─ Task 4  plugin Home Assistant + provider HA Media Players
                 └─ Task 5  fournisseur YouTube Music
                      └─ Task 6  les 4 lecteurs + le groupe
                           └─ Task 7  intégration music_assistant, entity_id fixés
                                └─ Task 8  ══ VALIDATION SONORE (porte) ══
                                     ├─ Task 9   les 4 plugins restants
                                     ├─ Task 10  retrait ytube + 3 fichiers YAML
                                     ├─ Task 11  wallpanel-app (pieces.ts)
                                     └─ Task 12  dashboards (rooms.py)
                                          └─ Task 13  README, CLAUDE.md, clôture
```

Le ménage du registre (Task 3) passe **avant** toute configuration de Music
Assistant : il crée `media_player.bar_de_son` et `media_player.enceinte_chambre`,
sur lesquels les lecteurs MA du salon et de la chambre sont bâtis. Le faire
après aurait donné à MA des noms d'entités qui changent sous lui.

La Task 8 est une **porte**, pas une étape : rien de ce qui suit n'est
réversible à bon compte, et tout ce qui précède laisse la maison avec sa
musique intacte.
