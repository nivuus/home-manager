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

> **État au 2026-09-05 — chantier clos à deux gestes près.**
> La **porte de la Task 8 est franchie** : confirmation humaine du propriétaire,
> 4 destinations sur 5 (le salon reste à attester). Le **dernier filet est
> retiré** — entrée de configuration `yTubeMusic` supprimée et dépôt
> `ytube_music_player` désinstallé de HACS le 2026-09-05, Task 10 Step 2.
> Le retour arrière n'est plus un repli : lire
> *[Ce que le retour arrière coûte désormais](#ce-que-le-retour-arrière-coûte-désormais--écrit-le-2026-09-05)*
> **avant** d'en avoir besoin.
> Restent **deux cases**, toutes deux au propriétaire : Task 9 Step 4
> (OAuth Last.fm) et Task 12 Step 7 (contrôle visuel sur la tablette du salon).
> Elles bloquent l'archivage de `tools/wallpanel/` (plan `home-desk`, Task 12
> Steps 5-6).

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

- [x] **Step 1: Poser la variable et créer le venv**

```bash
export SCRATCH=/tmp/user/0/claude-0/-home-mallanic-Projects-Nivuus-packages-home-manager/32c35198-ec87-4120-a49b-26bc01512a95/scratchpad
python3 -m venv "$SCRATCH/venv"
"$SCRATCH/venv/bin/pip" -q install websockets
"$SCRATCH/venv/bin/python" -c "import websockets; print(websockets.__version__)"
```

Attendu : un numéro de version (17.1 au moment de l'écriture).

- [x] **Step 2: Extraire les identifiants HA**

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

- [x] **Step 3: Écrire le client websocket**

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

- [x] **Step 4: Vérifier l'accès en lecture**

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

- [x] **Step 1: Écrire le test qui échoue**

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

- [x] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `python3 tests/test_compose_portable.py`
Expected: FAIL — `les six services sont declares: got [...], want [...]` puis
un `KeyError: 'bgutil-pot-provider'`.

- [x] **Step 3: Ajouter les deux services**

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
  # rouage interne de MA. L'image n'expose que -p/--port : le serveur ecoute
  # deja toutes les interfaces a l'interieur du conteneur, et c'est la
  # publication en boucle locale qui restreint, pas un flag de commande.
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
```

- [x] **Step 4: Lancer la suite complète**

Run: `make test`
Expected: les cinq suites passent, dont `test_compose_portable: OK`.

- [x] **Step 5: Commit**

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

- [x] **Step 1: Sauvegarder le compose de production**

```bash
sudo -n cp -a /opt/nivuus/home-manager/docker-compose.yml \
  /opt/nivuus/home-manager/docker-compose.yml.backup-music-assistant-20260903
```

- [x] **Step 2: Déposer le nouveau compose**

`install.py` n'est **pas** utilisé (voir Global Constraints).

```bash
sudo -n cp /home/mallanic/Projects/Nivuus/packages/home-manager/stack/docker-compose.yml \
  /opt/nivuus/home-manager/docker-compose.yml
sudo -n grep -c "" /opt/nivuus/home-manager/docker-compose.yml
```

- [x] **Step 3: Vérifier que la pile reste cohérente avant de démarrer quoi que ce soit**

```bash
cd /opt/nivuus/home-manager && sudo -n docker compose config --services | sort
```

Expected : les 8 services, `bgutil-pot-provider` et `music-assistant` inclus.
Le `.env` porte encore la surcouche de dev : si cette commande échoue sur un
fichier manquant, **arrêter** et vérifier que `docker-compose.dev.yml` est
toujours présent dans `DEPLOY`.

- [x] **Step 4: Démarrer les deux nouveaux services seulement**

```bash
cd /opt/nivuus/home-manager && sudo -n docker compose up -d music-assistant bgutil-pot-provider
sudo -n docker ps --filter name=music-assistant --filter name=bgutil --format '{{.Names}}\t{{.Status}}'
```

- [x] **Step 5: Vérifier les deux points d'entrée**

```bash
curl -s -o /dev/null -w "MA 8095 : HTTP %{http_code}\n" http://127.0.0.1:8095/
curl -s -o /dev/null -w "PO 4416 : HTTP %{http_code}\n" http://127.0.0.1:4416/ping
```

Expected : `HTTP 200` pour les deux.

- [x] **Step 6: Vérifier que le PO provider n'est PAS joignable depuis le LAN**

```bash
IP=$(ip -4 addr show scope global | grep -oP '(?<=inet )\d+\.\d+\.\d+\.\d+' | head -1)
curl -s -m 3 -o /dev/null -w "depuis $IP : %{http_code}\n" "http://$IP:4416/ping" || echo "refuse — attendu"
```

Expected : refus de connexion, ou `000`. Une réponse `200` ici est une
régression de sécurité : revenir à la Task 1.

- [x] **Step 7: Lire les journaux de MA**

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

- [x] **Step 1: Sauvegarder le registre**

```bash
sudo -n cp -a /opt/nivuus/home-manager/config/.storage/core.entity_registry \
  /opt/nivuus/home-manager/config/.storage/core.entity_registry.backup-music-assistant-20260903
```

- [x] **Step 2: Supprimer les trois enceintes mortes**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[
 {"type":"config/entity_registry/remove","entity_id":"media_player.enceinte_bureau"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.enceinte_bureau_2"},
 {"type":"config/entity_registry/remove","entity_id":"media_player.enceinte_chambre"}
]'
```

`enceinte_bureau_2` est un **groupe Cast**, pas une enceinte — c'est bien une
suppression d'entité, pas un débranchement de matériel.

- [x] **Step 3: Supprimer les 11 Play-Fi fantômes**

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

- [x] **Step 4: Réactiver et renommer la barre de son**

```bash
"$SCRATCH/venv/bin/python" "$SCRATCH/hareg.py" '[
 {"type":"config/entity_registry/update",
  "entity_id":"media_player.playfi2device2379390371",
  "disabled_by":null,
  "name":"Bar de son",
  "new_entity_id":"media_player.bar_de_son"}
]'
```

- [x] **Step 5: Renommer l'enceinte de la chambre**

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

- [x] **Step 6: Vérifier l'état final**

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

- [x] **Step 1: Ouvrir l'interface de MA**

`http://127.0.0.1:8095` — ou l'adresse LAN du serveur depuis un poste.
Premier lancement : MA demande de créer le compte administrateur local.

- [x] **Step 2: Ajouter le plugin Home Assistant**

Paramètres → Plugins → Ajouter → **Home Assistant**.
URL demandée : `https://home.allanic.me`.

**Étape opérateur** : MA ouvre la page d'autorisation de Home Assistant. Elle
demande une connexion dans le navigateur — c'est la première des trois
étapes qui ne peuvent pas être automatisées.

- [x] **Step 3: Ajouter le provider Home Assistant Media Players**

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

- [x] **Step 4: Vérifier que MA voit les lecteurs**

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

- [x] **Step 1: Extraire le cookie**

**Étape opérateur**, la deuxième des trois. Dans une fenêtre de navigation
**privée** : se connecter à `https://music.youtube.com`, ouvrir les outils de
développement, onglet Réseau, recharger, prendre une requête vers
`music.youtube.com`, et copier **la valeur entière de l'en-tête `Cookie`** —
elle doit contenir `__Secure-3PAPISID`.

La navigation privée n'est pas un détail : un cookie de session ordinaire est
invalidé dès que le navigateur se déconnecte ou renouvelle sa session.

- [x] **Step 2: Ajouter le fournisseur**

Paramètres → Fournisseurs → Ajouter → **YouTube Music**.

| Champ | Valeur |
|---|---|
| Username | l'adresse Gmail du compte Premium |
| Cookie | la valeur copiée au Step 1 |
| PO Token server URL | **laisser vide** — le défaut `http://127.0.0.1:4416` correspond au bind |

- [x] **Step 3: Vérifier que la configuration passe**

```bash
sudo -n docker logs music-assistant 2>&1 | tail -40
```

Expected : pas de `LoginFailed`. Deux échecs à distinguer :

- « PO Token server URL is not reachable » → le conteneur
  `bgutil-pot-provider` ne répond pas, revenir à la Task 2 Step 5 ;
- une erreur d'authentification → le cookie est mauvais ou expiré, reprendre
  le Step 1 dans une fenêtre privée neuve.

- [x] **Step 4: Vérifier la bibliothèque**

Dans l'UI, onglet *Musique* : les playlists du compte apparaissent, dont
« Mon supermix ». Noter son URI MA — elle sert à la Task 10.

- [x] **Step 5: Régler la langue et le mélange**

Reprise de l'ancienne configuration : langue **`fr`**, lecture aléatoire
activée par défaut sur les files, limite de file **25**.

---

### Task 6 : Les quatre lecteurs et le groupe

**Files:** aucun fichier du dépôt.

**Interfaces:**
- Consumes: le provider de la Task 4.
- Produces: 5 lecteurs MA, dont le groupe, consommés par la Task 7.

- [x] **Step 1: Renommer les quatre lecteurs dans MA**

Paramètres → Lecteurs. Le nom MA commande l'`entity_id` que créera
l'intégration côté HA : le préfixe `Musique` est ce qui empêche la collision
avec les entités Cast sous-jacentes, qui gardent leurs noms.

| Lecteur (entité HA sous-jacente) | Nom MA |
|---|---|
| `media_player.bar_de_son` | `Musique Salon` |
| `media_player.enceinte_cuisine` | `Musique Cuisine` |
| `media_player.bureau` | `Musique Chambre` |
| `media_player.google_home_salle_de_bain` | `Musique Salle de bain` |

- [x] **Step 2: Créer le groupe**

Paramètres → Lecteurs → Créer un groupe. Nom : **`Musique Maison`**.
Membres : les quatre lecteurs ci-dessus.

Ce groupe remplace le groupe Cast `media_player.maison`, cassé depuis que les
enceintes du bureau ont disparu. La synchronisation est celle de MA sur quatre
flux HTTP séparés, conséquence assumée de la contrainte « uniquement les
providers HA » : un léger décalage entre pièces est possible.

- [x] **Step 3: Vérifier**

L'onglet *Lecteurs* montre cinq entrées : les quatre pièces et `Musique Maison`.

---

### Task 7 : Intégration `music_assistant` côté Home Assistant

**Files:**
- Modify: registre d'entités de HA (par websocket)

**Interfaces:**
- Consumes: les 5 lecteurs de la Task 6, `$SCRATCH/hareg.py` de la Task 0.
- Produces: les `entity_id` `media_player.musique_*`, consommés par les
  Tasks 10 à 12.

- [x] **Step 1: Ajouter l'intégration**

Dans HA : Paramètres → Appareils et services → Ajouter une intégration →
**Music Assistant**. L'intégration est **core** depuis HA 2024.12 — ne pas
passer par HACS. Elle découvre le serveur local ; sinon saisir
`http://127.0.0.1:8095`.

- [x] **Step 2: Relever les `entity_id` réellement créés**

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

- [x] **Step 3: Fixer les cinq `entity_id`**

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

- [x] **Step 4: Vérifier les cinq entités**

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

> ## ══ PORTE FRANCHIE — 2026-09-05, confirmation humaine, 4 destinations sur 5 ══
>
> Le propriétaire a joué le script de validation et a confirmé **« Ça marche. »**
> C'est une oreille humaine, ce qu'aucun log ne remplace : la porte est franchie
> et la Task 10 Step 2 a été jouée le même jour.
>
> **Ce qui est attesté et ce qui ne l'est pas.** La sortie du script, arrivée
> après la confirmation orale, est plus précise qu'elle :
>
> ```
> media_player.musique_cuisine         playing | Casio
> media_player.musique_chambre         playing | Comic sans MS
> media_player.musique_salle_de_bain   playing | Comic sans MS
> media_player.musique_salon           off | None            <-- ici
> media_player.musique_maison          playing | Comic sans MS   (le groupe)
>
> === etats finaux ===
> musique_salon           idle | Comic sans MS
> musique_cuisine         idle | Casio
> musique_chambre         idle | Comic sans MS
> musique_salle_de_bain   idle | Comic sans MS
> musique_maison          idle | Comic sans MS
> ```
>
> Script sorti en **code 0**. Quatre destinations ont joué, plus le groupe — donc
> **la chaîne entière est prouvée**, de YouTube Music à l'enceinte. Mais le salon
> n'a rien joué à son tour, et la confirmation orale du propriétaire ne peut pas
> porter sur lui. Ni un échec, ni un succès complet : **4 sur 5**.
>
> **Le salon reste à attester.** Une mesure d'une minute, enceinte allumée.

- [x] **Step 1: Jouer sur chaque pièce** — 2026-09-05, 3 pièces sur 4

Depuis l'UI de MA, lancer une piste sur `Musique Cuisine`, puis
`Musique Chambre`, puis `Musique Salle de bain`, puis `Musique Salon`.

Vérifier **à l'oreille** que le son sort de la bonne enceinte. Une entité
`bar_de_son` qui répond sans qu'aucun son ne sorte est le symptôme d'une
Play-Fi fantôme : vérifier que c'est bien l'UUID `25992067-…` qui a été
réactivé à la Task 3.

#### Le cas du salon : deux hypothèses, une mesure pour les départager

Le relevé montre `musique_salon` à `off` **pendant son tour**, puis
`idle | Comic sans MS` à l'état final. Il a donc **reçu le média** mais n'était
pas allumé au moment de jouer. Deux lectures possibles :

| # | Hypothèse | Ce qu'elle prédit |
|---|---|---|
| A | **L'appareil était éteint ou en veille profonde.** Le lecteur MA ne fait que refléter l'état de l'entité Cast sous-jacente `media_player.bar_de_son`. | En rejouant barre allumée, `bar_de_son` **et** `musique_salon` passent tous deux à `playing`, et le son sort. |
| B | **Le lecteur MA du salon est mal câblé** — bâti sur une Play-Fi fantôme plutôt que sur l'UUID `25992067-…` réactivé à la Task 3. Il répondrait sans jamais sonner. | `musique_salon` annoncerait `playing` alors que `bar_de_son` resterait `off` ou `unavailable` — les deux états **divergeraient**. |

**La mesure qui tranche** : lire `media_player.musique_salon` et
`media_player.bar_de_son` **au même instant** pendant une lecture. Corrélés →
hypothèse A ; divergents → hypothèse B.

**Ce que cette mesure donne déjà, sans rejouer** (relevé du 2026-09-05 à 10:40,
après le retrait de `yTubeMusic`) :

```
lecteur MA                 etat       entite Cast sous-jacente       etat       correle
musique_salon              off        bar_de_son                     off        oui
musique_cuisine            off        enceinte_cuisine               off        oui
musique_chambre            off        enceinte_chambre               off        oui
musique_salle_de_bain      off        google_home_salle_de_bain      off        oui
```

Les quatre paires sont corrélées **au repos**, et six minutes plus tôt le même
relevé donnait `musique_cuisine` à `off` pendant que les trois autres étaient à
`idle` : le `off` **tourne** d'une enceinte à l'autre au fil de leur mise en
veille. C'est le comportement d'appareils qui s'endorment, pas d'un câblage
faux — et le câblage salon → `bar_de_son` est nommément correct. Un
`media_player.turn_on` sur un Cast a d'ailleurs expiré à 10:31
(`_start_app CC1AD845 timed out after 10.0 s`), ce qui ressemble à un appareil
qui ne se réveille pas.

**L'hypothèse A est donc la plus probable, mais elle n'est pas tranchée** : la
corrélation au repos ne vaut pas corrélation en lecture. Seule la mesure
ci-dessus, barre allumée, la ferme. Elle appartient au propriétaire.

- [x] **Step 2: Jouer sur le groupe** — 2026-09-05, `musique_maison` a joué

C'est ce step qui porte la preuve de bout en bout : le groupe a sonné.

- [x] **Step 3: Vérifier depuis Home Assistant** — 2026-09-05

```bash
TOK=$(python3 -c "import json;print(json.load(open('$SCRATCH/ha.json'))['token'])")
for e in musique_salon musique_cuisine musique_chambre musique_salle_de_bain musique_maison; do
  curl -s -H "Authorization: Bearer $TOK" "https://home.allanic.me/api/states/media_player.$e" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['entity_id']:38} {d['state']}\")"
done
```

Expected : cinq entités, aucune en `unavailable`.

Les cinq entités ont répondu, **aucune en `unavailable`**.

- [x] **Step 4: Porte** — franchie le 2026-09-05

Franchie sur la confirmation humaine et sur le groupe qui a joué. La réserve du
salon **n'a pas retenu la suite**, et c'est délibéré : le groupe a sonné, quatre
lecteurs sur cinq ont sonné, et `ytube_music_player` n'aurait de toute façon pas
réparé une enceinte éteinte. Le retrait de la Task 10 Step 2 a suivi le même
jour.

---

### Task 9 : Les quatre plugins restants

**Files:** aucun fichier du dépôt.

**Interfaces:**
- Consumes: le plugin Home Assistant de la Task 4 (moteurs IA et TTS).

- [x] **Step 1: AI Radio**

Paramètres → Plugins → Ajouter → **AI Radio**.

| Réglage | Valeur |
|---|---|
| Moteur IA | `conversation.gemini_flash` (via le plugin Home Assistant) |
| Moteur TTS | `tts.google_ai_tts` |
| Ville / pays | Paris, France — pour les bulletins météo |

Le plugin refuse de dépasser son premier écran sans un moteur IA **et** un
moteur TTS : si les listes sont vides, le plugin Home Assistant de la Task 4
n'est pas correctement connecté.

- [x] **Step 2: Party**

Paramètres → Plugins → Ajouter → **Party**. Lecteur : `Musique Maison`.
Une instance par lecteur est possible ; une seule sur le groupe suffit.

- [x] **Step 3: Music Quiz**

Paramètres → Plugins → Ajouter → **Music Quiz**. Lecteur : `Musique Maison`.

- [ ] **Step 4: LastFM Scrobbler** — ⚠️ RESTE AU PROPRIÉTAIRE, un seul geste

**Pourquoi personne d'autre ne peut le faire :** l'autorisation OAuth Last.fm
s'obtient dans un navigateur, sur un compte dont l'agent n'a pas et ne doit pas
avoir les identifiants. Aucun jeton n'a été inventé ni simulé.

**Le geste, dans l'ordre, une fois :**

1. Ouvrir l'interface de Music Assistant : `http://127.0.0.1:8095` depuis le
   serveur, ou l'adresse LAN du serveur sur le port 8095 depuis un poste — la
   même que celle utilisée aux Tasks 4 à 6.
2. **Paramètres → Plugins → Ajouter → LastFM Scrobbler**.
3. Choisir **Last.FM** — *et non LibreFM*, ce sont deux entrées voisines dans
   la même liste.
4. Choisir l'utilisateur MA à scrobbler.
5. Le plugin ouvre l'autorisation Last.fm : se connecter et **autoriser**.

**Vérification, à coller telle quelle après coup :**

```bash
sudo -n docker logs music-assistant 2>&1 | tail -40
sudo -n grep -o '"lastfm[a-z_]*"' \
  /opt/nivuus/home-manager/config/music_assistant/settings.json | sort -u
```

Attendu : un plugin `lastfm_scrobbler` dans les réglages, et aucun traceback
dans le journal. **Piège nommé** : `lastfm_recommendations` est déjà présent et
n'est *pas* le scrobbler — c'est un fournisseur de métadonnées livré par défaut.
Le voir seul signifie que le geste n'a pas été fait.

Rien d'autre dans ce chantier n'attend ce step : il est isolé, et son échec ne
coûte que le scrobbling.

- [x] **Step 5: Vérifier**

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

- [x] **Step 1: Sauvegarder les trois fichiers**

```bash
C=/opt/nivuus/home-manager/config
for f in scripts.yaml configuration.yaml custom_templates/wallpanel.jinja; do
  sudo -n cp -a "$C/$f" "$C/$f.backup-music-assistant-20260903"
done
```

- [x] **Step 2: Retirer l'entrée de configuration** — 2026-09-05, le dernier filet

**C'était le seul geste irréversible du chantier.** Il a attendu la porte de la
Task 8, comme le plan l'exigeait, et a été joué le jour où elle est tombée.

Le plan prescrivait l'UI (Paramètres → Appareils et services → **yTubeMusic** →
Supprimer, puis HACS → dépôt → Supprimer). Fait par API et websocket, ce qui
donne une trace vérifiable plutôt qu'un clic.

**État mesuré avant de toucher à quoi que ce soit** (les trois points que la
session du 2026-09-04 avait relevés, revérifiés un à un) :

| Point | Mesure du 2026-09-05 |
|---|---|
| `config/custom_components/ytube_music_player/` | absent du disque — confirmé |
| entrée `01KTBX6VVRXYP6SK6KX58CE7GH` | présente, **`state: not_loaded`** |
| ses entités | les 4 en `unavailable` |
| HACS `KoljaWindeler/ytube_music_player` | `installed: true`, commit `8aa412f`, `20260816.01` — le filet, intact |

Le corollaire annoncé s'est **réalisé** : Home Assistant avait redémarré à 10:17
et l'entrée était déjà retombée en `not_loaded`, son code n'étant plus sur le
disque. Sans conséquence — plus rien ne la référence.

**Sauvegarde datée**, six fichiers de `.storage/`, suffixe
`.backup-20260905-retrait-ytube` : `core.config_entries`, `core.entity_registry`,
`core.device_registry`, `hacs.data`, `hacs.repositories`, `hacs.hacs`. Vérifiée :
taille identique à l'octet près et JSON relu sans erreur sur les six.

**1. L'entrée de configuration**, par l'API REST depuis le conteneur :

```bash
S=<scratchpad>
TOK=$(sudo -n python3 -c "import json;print(json.load(open('$S/ha.json'))['token'])")
sudo -n docker exec -i -e TOK="$TOK" homeassistant python - <<'EOF'
import asyncio, os, aiohttp
ENTRY = "01KTBX6VVRXYP6SK6KX58CE7GH"
async def main():
    h = {"Authorization": "Bearer " + os.environ["TOK"]}
    async with aiohttp.ClientSession() as s:
        async with s.delete(f"http://127.0.0.1:8123/api/config/config_entries/entry/{ENTRY}", headers=h) as r:
            print("DELETE", r.status, await r.text())
asyncio.run(main())
EOF
```

→ `DELETE 200 {"require_restart":false}`. Entrées : **115 → 114**. Les quatre
entités du registre (`media_player.ytube_music_player`, les trois `select.`)
sont parties avec elle.

**2. Le dépôt HACS**, par websocket. HACS 2.0.5 expose
`hacs/repository/remove`, dont l'`uninstall()` gère un dossier déjà absent
(`Presumed local content path does not exist`) :

```bash
sudo -n "$S/venv/bin/python" "$S/hareg.py" \
  '[{"type":"hacs/repository/remove","repository":"315447202"}]'
```

→ `[{}]`, code 0.

**3. Preuve que le filet est parti.** Le dépôt n'est pas effacé du catalogue
HACS — il y reste comme les 3 249 autres intégrations *connues mais non
installées*. Ce qui compte est que les marqueurs d'installation ont disparu :

```
avant : {"id":"315447202", …, "installed_commit":"8aa412f", "installed":true,
         "last_version":"20260816.01", "version_installed":"20260816.01"}
apres : {"id":"315447202", "full_name":"KoljaWindeler/ytube_music_player"}
```

Plus aucune trace de `ytube` dans le registre d'entités ni dans les états —
les deux entités que HACS possédait (`update.ytube_music_player_update`,
`switch.ytube_music_player_pre_release`) sont parties avec le dépôt.

**Ce qui n'a pas été fait, et pourquoi.** Aucun redémarrage. `require_restart`
valait `false`, le retrait a pris effet à chaud et est vérifié à la fois dans
l'instance qui tourne et sur le disque. Surtout, `configuration.yaml` et
`custom_templates/wallpanel.jinja` avaient été modifiés à 10:21 par la session
`home-desk`, **après** le démarrage de 10:17 : redémarrer aurait mis en
production le travail en vol d'un autre chantier comme effet de bord du mien.
Les deux modifications sont des commentaires et `check_config` passe, mais ce
n'est pas à cette tâche de les publier.

Conséquence honnête : que le prochain démarrage soit **silencieux** n'est pas
*observé*, il est *déduit* — l'entrée qui criait n'existe plus dans
`core.config_entries`, sa cause est retirée par construction.

**Résidu traité le même jour.** Deux fichiers de jetons dormaient encore dans
`.storage/`, sauvegardes du jeton vif supprimé au Step 6 :

| Fichier | Date | Contenu | Mode |
|---|---|---|---|
| `header_ytube_music_player.json.bak_socs` | 11 juin | en-têtes complets, dont `cookie` et `authorization` | `-rw-r--r--` |
| `header_ytube_music_player.json.oauth_bak` | 5 juin | `access_token` (expiré le 2026-06-05) **et `refresh_token`**, scope `.../auth/youtube` | `-rw-r--r--` |

Des identifiants valides sans consommateur, et **lisibles par tous** sur
l'hôte. Même hygiène que les trois JWT `bleuenn_url_*` révoqués la veille.

**Garde passée avant de retirer** : aucun consommateur vivant. `header_ytube`
n'apparaît nulle part dans `tools/`, ni dans le `core.config_entries` vivant —
seulement dans deux **sauvegardes** de ce fichier (instantanés de l'entrée
supprimée) et dans le spec de ce chantier.

Archivés puis retirés :

```bash
A=/opt/nivuus/home-manager/backups-retrait-ytube-20260905   # 0700, fichiers 0600
sudo -n rm -f /opt/nivuus/home-manager/config/.storage/header_ytube_music_player.json.bak_socs \
              /opt/nivuus/home-manager/config/.storage/header_ytube_music_player.json.oauth_bak
```

L'archive suit la convention du `backups-home-desk-20260905` voisin : dossier
daté, frère de `config/`, root seul. Empreintes SHA-256 vérifiées identiques
avant retrait. Après : plus aucun `header_ytube` dans `.storage/`,
`check_config` **code 0**, les cinq lecteurs répondent, aucune en
`unavailable`.

> ⚠️ **Supprimer le fichier n'est pas révoquer le jeton.** Le `refresh_token`
> reste valide **côté Google** jusqu'à révocation explicite : un
> `refresh_token` ne porte pas de date d'expiration, contrairement à
> l'`access_token` qui, lui, a expiré le 2026-06-05. La copie locale a
> disparu ; l'autorisation, non. La révoquer se fait sur
> <https://myaccount.google.com/permissions>, et n'appartient qu'au
> propriétaire. Ce n'est **pas** une case de ce plan — c'est une hygiène de
> compte, sans effet sur Music Assistant, qui utilise un cookie distinct
> obtenu à la Task 5.

- [x] **Step 3: Basculer le script du Supermix**

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

- [x] **Step 4: Basculer le déclencheur du capteur *Wallpanel héros***

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

- [x] **Step 5: Basculer la macro `media_en_cours()`**

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

- [x] **Step 6: Supprimer le code et le jeton de l'intégration**

```bash
C=/opt/nivuus/home-manager/config
sudo -n rm -rf "$C/custom_components/ytube_music_player"
sudo -n rm -f "$C/.storage/header_ytube_music_player.json"
```

- [x] **Step 7: Vérifier la configuration puis recharger**

```bash
sudo -n docker exec homeassistant hass --script check_config -c /config 2>&1 | tail -20
```

Expected : `Testing configuration at /config` puis aucune erreur.

```bash
TOK=$(python3 -c "import json;print(json.load(open('$SCRATCH/ha.json'))['token'])")
curl -s -X POST -H "Authorization: Bearer $TOK" https://home.allanic.me/api/services/script/reload
curl -s -X POST -H "Authorization: Bearer $TOK" https://home.allanic.me/api/services/template/reload
```

- [x] **Step 8: Vérifier que la macro rend bien**

```bash
TOK=$(python3 -c "import json;print(json.load(open('$SCRATCH/ha.json'))['token'])")
curl -s -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"template":"{% from '"'"'wallpanel.jinja'"'"' import media_en_cours %}{{ media_en_cours() }}"}' \
  https://home.allanic.me/api/template
```

Expected : `true` ou `false`, **pas** une erreur de template.

- [x] **Step 9: Vérifier qu'il ne reste aucune référence vivante**

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

- [x] **Step 1: Sauvegarder**

```bash
sudo -n cp -a /opt/nivuus/HomeAssistant/data/tools/wallpanel-app/src/pieces.ts \
  /opt/nivuus/HomeAssistant/data/tools/wallpanel-app/src/pieces.ts.backup-music-assistant-20260903
sudo -n cp -a /opt/nivuus/home-manager/config/www/wallpanel \
  /opt/nivuus/home-manager/config/www/wallpanel.backup-music-assistant-20260903
```

- [x] **Step 2: Salon — remplacer le bloc `sources` (vers la ligne 211)**

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

- [x] **Step 3: Bureau — remplacer le bloc `sources` (vers la ligne 315)**

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

- [x] **Step 4: Cuisine — remplacer le bloc `sources` (vers la ligne 407)**

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

- [x] **Step 5: Lancer les tests de l'application**

```bash
cd /opt/nivuus/HomeAssistant/data/tools/wallpanel-app && sudo -n npm run test
```

Expected : la suite `vitest` passe. Un test qui référence
`media_player.ytube_music_player` doit être mis à jour vers `musique_salon`.

- [x] **Step 6: Reconstruire**

```bash
cd /opt/nivuus/HomeAssistant/data/tools/wallpanel-app && sudo -n npm run build
```

- [x] **Step 7: Vérifier que le généré est propre**

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

- [x] **Step 1: Sauvegarder**

```bash
sudo -n cp -a /opt/nivuus/HomeAssistant/data/tools/wallpanel/rooms.py \
  /opt/nivuus/HomeAssistant/data/tools/wallpanel/rooms.py.backup-music-assistant-20260903
C=/opt/nivuus/home-manager/config/.storage
for p in salon bureau cuisine; do
  sudo -n cp -a "$C/lovelace.wallpanel_$p" "$C/lovelace.wallpanel_$p.backup-music-assistant-20260903"
done
```

- [x] **Step 2: Retirer le sélecteur de source de `_lecteur_media`**

Ligne 29 de `rooms.py`, dans le dict rendu par `_lecteur_media` : supprimer la
ligne `"source": "icon",`. Elle affichait le sélecteur d'enceinte de
`ytube_music_player`, que MA n'a pas — un lecteur MA par pièce le remplace.

- [x] **Step 3: Basculer `_MEDIA` (lignes 41-53)**

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

- [x] **Step 4: Générer sans déployer**

```bash
cd /opt/nivuus/HomeAssistant/data && sudo -n python3 tools/wallpanel/build.py
echo -n "ytube dans les generes : "
sudo -n grep -rc "ytube" tools/wallpanel/genere/ | grep -v ":0" || echo "aucun"
```

Expected : `aucun`.

- [x] **Step 5: Déployer les trois dashboards**

`build.py --deploy` valide que **toute** entité utilisée existe réellement dans
HA avant d'écrire, et abandonne sinon. C'est le filet de sécurité de cette
tâche : un `musique_*` mal nommé à la Task 7 fait échouer ici, pas en silence.

```bash
cd /opt/nivuus/HomeAssistant/data && sudo -n python3 tools/wallpanel/build.py --deploy salon bureau cuisine
```

Expected : trois lignes de génération, aucun « DÉPLOIEMENT ABANDONNÉ ».

- [x] **Step 6: Vérifier les dashboards déployés**

```bash
C=/opt/nivuus/home-manager/config/.storage
for p in salon bureau cuisine; do
  echo -n "$p — ytube : $(sudo -n grep -oc 'ytube' "$C/lovelace.wallpanel_$p" 2>/dev/null || echo 0)"
  echo "  musique_ : $(sudo -n grep -o 'musique_[a-z_]*' "$C/lovelace.wallpanel_$p" | sort -u | tr '\n' ' ')"
done
```

Expected : `ytube : 0` sur les trois.

- [ ] **Step 7: Vérifier à l'œil sur une tablette** — ⚠️ RESTE AU PROPRIÉTAIRE

**Pourquoi personne d'autre ne peut le faire :** c'est un contrôle visuel sur
un écran physique. Aucune capture n'a été prise, aucun contrôle n'a été simulé.

**Le geste, une fois** — et il se combine avec la réserve du salon de la
Task 8, ce qui en fait *une seule* visite au salon plutôt que deux :

1. Allumer la barre de son du salon et la mettre sur la bonne entrée.
2. Recharger la tablette du salon.
3. Lancer une piste sur `Musique Salon` depuis la tablette.
4. **Regarder trois choses** : la carte média apparaît ; la pochette s'affiche ;
   pause / suivant / volume agissent bien sur l'enceinte.
5. **Écouter** : le son sort de la barre de son. Ceci ferme la réserve « salon
   non attesté » de la Task 8.

**Mesure à lancer pendant que la piste joue** — c'est elle qui tranche entre
les hypothèses A et B de la Task 8 :

```bash
S=<scratchpad>
sudo -n "$S/venv/bin/python" "$S/hareg.py" '[{"type":"get_states"}]' | python3 -c "
import sys,json
st={s['entity_id']:s for s in json.load(sys.stdin)[0]}
for e in ['media_player.musique_salon','media_player.bar_de_son']:
    print(f\"{e:32} {st[e]['state']}\")
"
```

Attendu (hypothèse A, l'enceinte dormait) : **les deux à `playing`**. Si
`musique_salon` est à `playing` et `bar_de_son` ne l'est pas, c'est
l'hypothèse B — une Play-Fi fantôme — et il faut revenir à la Task 3.

---

### Task 13 : Documentation et clôture

**Files:**
- Modify: `README.md` (tableau des services)
- Modify: `CLAUDE.md` (section « Décisions à ne pas défaire »)

- [x] **Step 1: Compléter le tableau du README**

Sous `matterjs-server`, ajouter :

```markdown
| `music-assistant` | serveur musical, sur le port 8095 |
| `bgutil-pot-provider` | générateur de jetons YouTube, boucle locale seule |
```

Et corriger la phrase d'introduction : « Home Assistant et les **cinq**
services dont il dépend » devient « et les **sept** services dont il dépend ».

- [x] **Step 2: Ajouter les trois décisions à `CLAUDE.md`**

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

- [x] **Step 3: Lancer la suite complète**

```bash
make test NIVUUS_INSTALLER_DIR=/home/mallanic/Projects/Nivuus/packages/installer
```

Expected : les cinq suites passent.

Le chemin est écrit en absolu à dessein : `$HOME` vaut `/root` dans les
sessions d'agent, et un `NIVUUS_INSTALLER_DIR` inexistant ne lève rien —
`sys.path.insert` l'accepte en silence et le test échoue plus loin sur un
`ModuleNotFoundError: No module named 'packages'` qui ressemble à une
régression du dépôt `installer`.

- [x] **Step 4: Commit**

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

| # | Task | Étape | État au 2026-09-05 |
|---|---|---|---|
| 1 | Task 4 Step 2 | autorisation du plugin Home Assistant dans MA | ✅ fait |
| 2 | Task 5 Step 1 | extraction du cookie YouTube Music en navigation privée | ✅ fait |
| 3 | Task 9 Step 4 | autorisation OAuth Last.fm, dans un navigateur | ⚠️ **dû** |
| 4 | Task 12 Step 7 | contrôle visuel sur la tablette du salon | ⚠️ **dû** |

Le plan annonçait trois étapes opérateur, toutes dans un navigateur. Il y en a
**deux qui restent**, et la seconde n'est pas dans un navigateur : c'est un
écran physique. Elles sont détaillées à leur place, chacune réduite à un seul
geste, avec la commande de vérification à coller après coup.

**La quatrième absorbe une cinquième chose** : le contrôle visuel du salon
(Task 12 Step 7) se fait devant la même enceinte que la réserve de la Task 8
(le salon n'a pas joué à son tour). Une seule visite au salon ferme les deux.

### Ces deux gestes en bloquent un troisième, ailleurs

L'archivage de `tools/wallpanel/` — **Task 12 Steps 5-6 du plan `home-desk`**
(`packages/home-desk/docs/superpowers/plans/2026-09-04-package-home-desk.md`) —
attend que ce chantier-ci soit clos, pour ne pas déplacer un répertoire sous
les pieds d'un travail en vol.

Son Step 4 est une garde mécanique : il compte les cases décochées de *ce*
fichier et s'arrête si le compte n'est pas nul.

```bash
grep -c '^- \[ \]' docs/superpowers/plans/2026-09-03-music-assistant.md
```

Au 2026-09-05 il rend **2** — les deux lignes ci-dessus, et rien d'autre. Les
cocher débloque l'archivage sans autre condition.

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

**Cet ordre n'a pas été tenu** — les Tasks 10 (sauf Step 2), 11 et 12 ont
précédé la porte, franchie seulement le 2026-09-05. Seul le Step 2 de la
Task 10, le geste irréversible, a réellement attendu. Le journal en tire les
conséquences.

---

## Journal d'exécution — relevé du 2026-09-04

Cette section consigne l'état **mesuré**, pas l'état attendu. Elle existe
parce que le plan a été exécuté par plusieurs sessions et que l'ordre des
dépendances a été rompu.

### L'inversion : les Tasks 11 et 12 ont précédé la porte de la Task 8

Les Tasks 10 (sauf son Step 2), 11 et 12 ont été exécutées le 2026-09-04 par
une session antérieure, **avant** la validation sonore de la Task 8, alors que
le plan les en fait dépendre explicitement. Mesuré :

- `tools/wallpanel-app/src/pieces.ts` : 36 occurrences `musique_*`, 0 `ytube` ;
- bundle `config/www/wallpanel/wallpanel.js` reconstruit le 4 sept. à 01:01,
  0 `ytube`, contient `musique_salon`, `musique_cuisine`, `musique_maison` ;
- `tools/wallpanel/rooms.py` : 5 occurrences `musique_*`, 0 `ytube` ;
- les trois `.storage/lovelace.wallpanel_{salon,bureau,cuisine}` : 0 `ytube`.

Ces trois arbres (`TOOLS`) ne sont **pas versionnés dans ce dépôt** et portent
trois chantiers empilés dont deux sont étrangers à Music Assistant. Leur tri
revient au propriétaire, dans le cadre du futur package `home-desk`. Rien n'y
a été commité ni nettoyé ici.

Conséquence : la maison n'a plus de repli logiciel immédiat vers
`ytube_music_player` — les YAML et le wallpanel pointent déjà tous sur
`musique_*`. La porte de la Task 8 a donc perdu la moitié de sa fonction.

**Suite, 2026-09-05 :** la porte a été franchie (confirmation humaine, 4
destinations sur 5), et le filet HACS retiré le même jour. Ce que le retour
arrière coûte désormais est écrit plus bas — c'est la conséquence directe de
cette inversion, et elle n'est plus réparable.

### Ce qui était délibérément retenu — et qui a été joué le 2026-09-05

> **Cette section est conservée telle qu'elle a été écrite le 2026-09-04**, pour
> que la décision d'attendre reste lisible. Elle a été honorée : le Step 2 a
> attendu la porte, et n'a été joué qu'une fois la porte franchie. Le détail de
> l'exécution est à la Task 10 Step 2 ; le coût qu'elle laisse est juste après.

**Task 10 Step 2 — retrait de l'entrée de configuration `ytube_music_player`
et de son dépôt HACS.** Non exécuté au 2026-09-04, sciemment. Mesuré alors :

- `config/custom_components/ytube_music_player/` : **supprimé** ;
- l'entrée de configuration `01KTBX6VVRXYP6SK6KX58CE7GH` (`yTubeMusic`) :
  **toujours présente**, et ses six entités répondent encore parce que Home
  Assistant tourne depuis 6 jours et détient l'intégration en mémoire ;
- HACS liste toujours `KoljaWindeler/ytube_music_player` en
  `installed: true`, commit `8aa412f`, version `20260816.01`.

C'est cette dernière ligne qui fait la décision : tant que HACS porte le dépôt,
un retéléchargement plus un redémarrage restaurent l'intégration. Retirer
l'entrée et le dépôt est le seul geste de tout le chantier qui ne se rejoue pas
depuis la machine. Il attend donc la porte sonore, comme le plan l'exige.

Corollaire à ne pas perdre : **au prochain redémarrage de Home Assistant,
l'entrée `yTubeMusic` échouera au chargement**, son code n'étant plus sur le
disque. C'est bruyant mais sans conséquence — aucun YAML, dashboard ni
wallpanel ne la référence plus.

*Ce corollaire s'est réalisé au redémarrage du 2026-09-05 à 10:17 : l'entrée est
retombée en `not_loaded` et ses quatre entités en `unavailable`. Il est éteint
depuis : l'entrée n'existe plus.*

---

## Ce que le retour arrière coûte désormais — écrit le 2026-09-05

Cette section existe pour être lue **avant** d'en avoir besoin. Jusqu'au
2026-09-05, revenir à `ytube_music_player` coûtait **deux gestes** :
retélécharger le dépôt depuis HACS, redémarrer. C'était l'argument qui
autorisait tout le reste du chantier.

**Ce prix n'existe plus.** Le dépôt HACS a été retiré et l'entrée de
configuration supprimée. Il ne reste sur la machine aucune trace de
l'intégration : ni code, ni entrée, ni entité, ni marqueur d'installation.

### Le nouveau prix, poste par poste

| # | Ce qu'il faut refaire | Pourquoi ce n'est plus gratuit |
|---|---|---|
| 1 | **Réinstaller `KoljaWindeler/ytube_music_player` depuis HACS**, puis redémarrer HA | Le dépôt n'est plus `installed`. Il faut le retrouver dans le catalogue, le retélécharger — donc **dépendre de GitHub et de l'amont** : ni le commit `8aa412f` ni la version `20260816.01` ne sont conservés en local. Si l'amont a disparu ou changé, la version d'avant n'est **pas** récupérable. |
| 2 | **Recréer l'entrée de configuration** et sa connexion à YouTube Music | L'entrée `01KTBX6VVRXYP6SK6KX58CE7GH` est supprimée. Il faut repasser le flux de configuration, **cookie compris**. |
| 3 | **Retrouver les `entity_id` d'origine** | Les quatre entités (`media_player.ytube_music_player`, les trois `select.`) sont sorties du registre. Rien ne garantit qu'elles reviennent sans suffixe `_2` — le piège nommé au CLAUDE.md. |
| 4 | **Rebasculer les trois fichiers YAML** — `scripts.yaml`, `configuration.yaml`, `custom_templates/wallpanel.jinja` | Ils pointent tous sur `musique_*`. Sauvegardes `.backup-music-assistant-20260903` (Task 10 Step 1) — mais elles ont vieilli : d'autres chantiers ont écrit dans ces fichiers depuis, `configuration.yaml` et `wallpanel.jinja` encore le 2026-09-05 à 10:21 par `home-desk`. **Les restaurer en bloc perdrait ce travail-là.** La bascule inverse est à faire à la main. |
| 5 | **Rebasculer le wallpanel** — `tools/wallpanel-app/src/pieces.ts` (36 occurrences `musique_*`) puis reconstruire le bundle `config/www/wallpanel/wallpanel.js` | Cet arbre **n'est pas versionné dans ce dépôt**. Pas de `git revert` : la bascule inverse est manuelle, et elle porte trois chantiers empilés dont deux sont étrangers à Music Assistant. |
| 6 | **Rebasculer les trois dashboards** — `tools/wallpanel/rooms.py`, puis regénérer et redéployer `lovelace.wallpanel_{salon,bureau,cuisine}` | Même arbre non versionné, même absence de `git revert`. Et ce répertoire est lui-même **candidat à l'archivage** (`home-desk` Task 12 Steps 5-6) : une fois archivé, le retour arrière passe d'abord par le désarchivage. |

### Ce que ça veut dire en une phrase

Le retour arrière est passé d'**un redémarrage** à une **réinstallation depuis
un amont qu'on ne contrôle pas, plus six bascules dont trois dans du code non
versionné** — et il n'y a plus de moment où la maison garde sa musique pendant
qu'on répare. **Ce n'est plus un repli : c'est un reconstruire.**

### Le filet qui reste, et ce qu'il ne couvre pas

Ce qui a été supprimé le 2026-09-05 est restaurable, mais seulement au niveau
du magasin d'état, et seulement tant que ces fichiers existent :

```
/opt/nivuus/home-manager/config/.storage/*.backup-20260905-retrait-ytube
  core.config_entries   core.entity_registry   core.device_registry
  hacs.data             hacs.repositories      hacs.hacs
```

Restaurer `core.config_entries` et `hacs.repositories` **HA arrêté** ramène
l'entrée et le marqueur `installed: true` — mais **pas le code** de
l'intégration, effacé du disque dès le 2026-09-04 et jamais sauvegardé. Le
poste 1 du tableau reste donc dû dans tous les cas.

Et ces six fichiers sont des instantanés du **2026-09-05 à 10:2x**. Les
restaurer en bloc plus tard écraserait tout ce que le registre a appris
depuis. Ils dépannent une erreur constatée dans l'heure, pas un remords dans
un mois.

---

### Vérifications refaites de bout en bout le 2026-09-04

| Point | Mesure |
|---|---|
| compose déployé vs dépôt | identiques (`diff` vide) |
| services déclarés | les 8, `music-assistant` et `bgutil-pot-provider` inclus |
| conteneurs | `music-assistant` et `bgutil-pot-provider` *Up 24 hours* |
| MA `http://127.0.0.1:8095` | HTTP 200 |
| PO `http://127.0.0.1:4416/ping` | HTTP 200 |
| PO exposé au LAN ? | non — `docker port` donne `4416/tcp -> 127.0.0.1:4416`, `ss` confirme un seul `LISTEN 127.0.0.1:4416` |
| registre : `playfi*` fantômes | aucune restante |
| registre : `bar_de_son`, `enceinte_chambre` | présentes, actives |
| registre : les 5 `media_player.musique_*` | présentes, aucun suffixe `_2` |
| `ytube` dans les 5 YAML de `config/` | 0 partout |
| macro `media_en_cours()` | rend `false`, pas une erreur de template |
| `script.lancer_supermix_maison` | pointe `media_player.musique_maison`, URI `ytmusic--WVwemhvP://playlist/RDTMAK5uy_kset8DisdE7LSD4TNjEVvrKRTmG7a56sY🎵ytmusic--WVwemhvP` |
| `make test` | les cinq suites passent |

La vérification du Step 6 de la Task 2 a été faite par **inspection des
bindings** (`docker port` et `ss -lntp`) plutôt que par un `curl` vers
l'adresse LAN : c'est la même preuve en plus fort, et le `curl` sortant se
heurtait au profil zsh de la machine.

### État des fournisseurs et plugins de Music Assistant

Relevé dans `music_assistant/settings.json` :

- plugin **Home Assistant** (`hass`) : actif ;
- provider **HA Media Players** (`hass_players`) : actif, exactement les quatre
  entités prévues — `bar_de_son`, `enceinte_cuisine`, `enceinte_chambre`,
  `google_home_salle_de_bain` ;
- fournisseur **YouTube Music** (`ytmusic--WVwemhvP`) : actif, `username`,
  `cookie` et `po_token_server_url` renseignés ; bibliothèque peuplée (le
  journal montre le balayage de centaines d'artistes) ;
- lecteurs nommés `Musique Salon`, `Musique Cuisine`, `Musique Chambre`,
  `Musique Salle de bain`, plus le groupe `syncgroup_3h7twpvm` ;
- `chromecast` : **désactivé**, conformément à la contrainte « uniquement les
  providers HA » ;
- plugins **AI Radio** (moteur IA, moteur TTS et ville chiffrés dans
  `setup_data`, `timezone: Europe/Paris`), **Party** (`Musique Maison`,
  accès invité et mode karaoké activés) et **Music Quiz** : actifs ;
- langue : `core.metadata.language = fr_FR`.

**Manque : le plugin LastFM Scrobbler** (Task 9 Step 4). Seul
`lastfm_recommendations` est présent, et c'est un fournisseur de métadonnées
livré par défaut, pas le scrobbler. L'autorisation OAuth Last.fm reste due.

Le journal de MA ne porte aucun `LoginFailed`. Ses seules erreurs sont un
`AttributeError: 'MusicAssistant' object has no attribute 'remote_access'` et
des échecs de métadonnées Wikipédia, tous deux étrangers à ce chantier.

---

## Journal d'exécution — relevé du 2026-09-05

### Ce qui a été fait ce jour

1. **La porte de la Task 8 a été franchie** — confirmation humaine du
   propriétaire, contre 4 destinations sur 5 attestées par le script. Consigné
   à la Task 8, avec le relevé brut et la réserve du salon.
2. **Task 10 Step 2 joué** — entrée de configuration `yTubeMusic` supprimée,
   dépôt `ytube_music_player` désinstallé de HACS. C'était le dernier geste
   irréversible du chantier.
3. **Le coût du retour arrière** a été écrit, tant qu'il est encore connu.
4. **Les deux jetons morts retirés** de `.storage/`, après garde de
   non-référencement — détail à la Task 10 Step 2.

### Vérifications de bout en bout, après le retrait

| Point | Mesure |
|---|---|
| entrée `01KTBX6VVRXYP6SK6KX58CE7GH` | **supprimée** — `DELETE 200 {"require_restart":false}`, entrées 115 → 114 |
| dépôt HACS `315447202` | **désinstallé** — `hacs/repository/remove` → `[{}]`, code 0 ; `installed`, `installed_commit` et `version_installed` disparus de `hacs.data` et `hacs.repositories` |
| `ytube` dans le registre d'entités | **0** — les 4 entités de l'entrée et les 2 de HACS parties |
| `ytube` dans les états | **0** |
| composant `ytube_music_player` chargé ? | non — absent des 446 composants |
| sauvegardes datées | 6 fichiers `.storage/*.backup-20260905-retrait-ytube`, taille identique à l'octet et JSON relu sans erreur |
| `check_config` | **code 0**, aucune erreur, aucun avertissement |
| Home Assistant | `GET /api/` → 200 « API running », version 2026.8.3, `state: RUNNING` |
| composants `music_assistant` et `hacs` | tous deux **chargés** |
| les 5 `media_player.musique_*` | présentes, **aucune en `unavailable`** ni absente |
| erreurs HA depuis le retrait | aucune imputable au retrait |
| conteneurs | `music-assistant` et `bgutil-pot-provider` *Up 35 hours*, `homeassistant` *Up 22 minutes* (démarrage de 10:17, antérieur au retrait) |
| `make test` | **code 0**, les cinq suites `OK` |
| jetons morts `header_ytube_*.bak` | **retirés** — archivés en 0600 sous `backups-retrait-ytube-20260905/`, SHA-256 vérifiées ; `check_config` reste à **code 0**, les cinq lecteurs répondent |

### Les deux jetons morts, et ce que leur retrait ne fait pas

`header_ytube_music_player.json.bak_socs` portait un jeu d'en-têtes complet
(`cookie`, `authorization`) ; `.oauth_bak` portait un `access_token` expiré
**et un `refresh_token`** de scope `.../auth/youtube`. Tous deux en
`-rw-r--r--` : des identifiants valides, sans consommateur, lisibles par tous
sur l'hôte.

La garde a été passée avant de toucher quoi que ce soit : `header_ytube`
n'apparaît dans aucun fichier vivant — ni dans `tools/`, ni dans le
`core.config_entries` en service. Les seules occurrences sont dans deux
sauvegardes de ce fichier et dans le spec.

**Ce que le retrait ne fait pas :** il supprime la copie locale, **il ne
révoque pas l'autorisation**. Un `refresh_token` reste valide côté Google
jusqu'à révocation explicite — il ne porte pas de date d'expiration, à la
différence de l'`access_token` qui, lui, avait expiré le 2026-06-05. La
révocation se fait sur <https://myaccount.google.com/permissions> et
n'appartient qu'au propriétaire. Elle est **sans effet sur Music Assistant**,
qui s'authentifie par un cookie distinct obtenu à la Task 5 — et elle n'est
volontairement **pas** une case de ce plan, pour ne pas bloquer l'archivage
de `home-desk` sur une hygiène de compte.

### Ce qui n'a **pas** été fait, et pourquoi

- **Aucun redémarrage de Home Assistant.** `require_restart` valait `false` et
  le retrait est vérifié à chaud. Surtout, `configuration.yaml` et
  `custom_templates/wallpanel.jinja` avaient été modifiés à 10:21 par la session
  `home-desk`, après le démarrage de 10:17 : redémarrer aurait publié le travail
  en vol d'un autre chantier comme effet de bord de celui-ci. Conséquence
  assumée : le silence du prochain démarrage est **déduit**, pas observé.
- **Aucun son rejoué.** Rien n'a été lancé sur les enceintes : le propriétaire
  a déjà validé, et rejouer aurait sonné chez lui sans prévenir.

*Les deux `header_ytube_music_player.json.*bak*` figuraient d'abord dans cette
liste, laissés à décision. Ils ont été retirés le même jour — section
suivante.*

### Le cas du salon, mesuré sans rejouer

Les quatre lecteurs `musique_*` reflètent **exactement** l'état de leur entité
Cast sous-jacente, et le `off` tourne de l'une à l'autre au fil de leur mise en
veille (10:34 : cuisine `off`, les trois autres `idle` ; 10:40 : les quatre
`off`). Le câblage salon → `bar_de_son` est nommément correct. Un
`media_player.turn_on` sur un Cast a expiré à 10:31
(`_start_app CC1AD845 timed out after 10.0 s`).

Tout pointe vers une enceinte endormie plutôt qu'un défaut de Music Assistant
— **sans trancher** : la corrélation au repos ne vaut pas corrélation en
lecture. La mesure qui ferme la question est à la Task 12 Step 7, et elle
appartient au propriétaire.
