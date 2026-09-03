# Music Assistant dans `home-manager` — design

**Date** : 2026-09-03
**Statut** : en relecture

## Objectif

Remplacer `ytube_music_player` — intégration HACS qui porte aujourd'hui toute
la musique de la maison — par **Music Assistant**, déployé comme service du
package `home-manager`, relié à Home Assistant, et augmenté de cinq plugins :
`Home Assistant`, `AI Radio`, `Party`, `Music Quiz`, `LastFM Scrobbler`.

Le remplacement n'est pas un portage : les deux logiciels ne s'authentifient
pas de la même façon auprès de YouTube Music, et la flotte d'enceintes a changé
depuis que l'ancienne intégration a été configurée. Ce document dit ce qui se
transfère, ce qui se reconstruit, et ce qui ne peut venir que de l'opérateur.

## Ce qui existe

### L'intégration sortante

`ytube_music_player` (`config/custom_components/`, dépôt HACS) expose **un seul
lecteur virtuel**, `media_player.ytube_music_player`, dont on choisit
l'enceinte de destination par `media_player.select_source`. Sa configuration
utile :

| Clé | Valeur |
|---|---|
| authentification | **OAuth TV** (`client_id`, `secret`, `code`, `codeTT` remplis ; `cookie` **vide**) |
| `speakers` | 6 entités, dont 3 n'existent plus sur le réseau |
| `api_language` | `fr` |
| `shuffle` / `shuffle_mode` | `true` / `Shuffle Random` |
| `track_limit` | 25 |
| jeton | `config/.storage/header_ytube_music_player.json` |

Son empreinte, six points d'ancrage :

| Emplacement | Occurrences |
|---|---|
| `config/scripts.yaml` — script « Lancer Mon Supermix (Maison) » | 2 |
| `config/configuration.yaml` — déclencheur du capteur *Wallpanel héros* | 1 |
| `config/custom_templates/wallpanel.jinja` — macro `media_en_cours()` | 1 |
| `config/.storage/lovelace.wallpanel_{salon,bureau,cuisine}` | 4 × 3 |
| `config/www/wallpanel/wallpanel.js` (minifié, **généré**) | 18 |
| `/opt/nivuus/HomeAssistant/data/tools/wallpanel-app/src/pieces.ts` (**source**) | 18 |
| `/opt/nivuus/HomeAssistant/data/tools/wallpanel/rooms.py` (**source**) | 3 |

Les deux dernières lignes commandent les deux précédentes. Ne corriger que le
généré, c'est se faire écraser au prochain build.

### La flotte réelle

Relevé le 2026-09-03 par scan mDNS `_googlecast._tcp` croisé avec le registre
d'entités :

| Appareil | IP | Entité HA | État |
|---|---|---|---|
| Bar de son Philips B8805 | 192.168.0.142 | `media_player.playfi2device2379390371` | vivant, **entité désactivée** |
| Enceinte (Nest Mini) | 192.168.0.63 | `media_player.enceinte_cuisine` | vivant |
| Enceinte Chambre (Home Mini) | 192.168.0.202 | `media_player.bureau` | vivant |
| Salle de bain (Home Mini) | 192.168.0.210 | `media_player.google_home_salle_de_bain` | vivant |
| Maison (groupe Cast) | 192.168.0.142 | `media_player.maison` | **indisponible** |

Trois faits qui commandent le design :

1. **`media_player.bureau` est l'enceinte de la chambre** — device « Enceinte
   Chambre », `md=Google Home Mini`, à 192.168.0.202. Le nom d'entité ment
   déjà, avant qu'on y touche.
2. **Le bureau n'a plus d'enceinte.** `enceinte_bureau` (Home Mini) et
   `enceinte_bureau_2` (un groupe Cast, pas une enceinte) sont `unavailable`
   depuis le 2026-08-29 et absents du réseau, comme `enceinte_chambre`
   (« Enceinte Droite »).
3. **Le groupe Cast « Maison » est cassé** et ne guérira pas seul : il est
   hébergé par la barre de son, mais compte parmi ses membres les enceintes du
   bureau disparues.

S'y ajoutent **11 entités Play-Fi fantômes** : la barre de son a été découverte
trois fois (`2379390371`, `2379391336`, `2379394027` — seule la première
correspond au périphérique vivant, UUID `25992067-…`), et neuf autres entités
pointent des Philips TAW6205 absents.

### Il n'y a pas de filet

Plex n'expose que *Films* et *Séries TV* — **aucune bibliothèque musicale** —
et `media/` ne contient que quatre sons d'alarme. **YouTube Music est la seule
source musicale de la maison.** Toute étape qui la coupe coupe la musique.

## Décisions

Prises avec l'opérateur avant rédaction :

| Question | Décision |
|---|---|
| YouTube Music Premium | **actif** — le fournisseur MA est viable |
| Emplacement | dans **`stack/docker-compose.yml`** du package |
| Lecteurs MA | **uniquement** le provider *Home Assistant Media Players* |
| Wallpanel | source « Musique » par pièce **et** « Multiroom » sur le groupe |
| Bureau (sans enceinte) | sa source « Musique » pointe **la barre de son** |
| Groupe « Maison » | **groupe MA** bâti sur les lecteurs HA, pas le groupe Cast |
| Moteurs AI Radio | Gemini (`conversation.gemini_flash` + `tts.google_ai_tts`) |
| Ménage | supprimer les 3 enceintes mortes et les 11 Play-Fi fantômes ; réactiver et renommer la barre de son ; renommer `bureau` → `enceinte_chambre` |

## § 1 — La pile

Deux services ajoutés à `stack/docker-compose.yml`.

```yaml
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

  bgutil-pot-provider:
    container_name: bgutil-pot-provider
    image: brainicism/bgutil-ytdlp-pot-provider:latest
    restart: unless-stopped
    init: true
    ports:
      - "127.0.0.1:4416:4416"
    command: ["--host", "0.0.0.0"]
```

`network_mode: host` pour `music-assistant` n'est pas décoratif : MA sert ses
flux aux enceintes depuis le port 8097, et un conteneur en réseau bridge leur
annoncerait une adresse qu'elles ne savent pas joindre. Les ports 8095 (UI) et
8097 (flux) ont été vérifiés libres sur l'hôte.

**Le PO token server est lié à la boucle locale**, `127.0.0.1:4416`, même
raisonnement que `docker-socket-proxy` : c'est un rouage interne de MA, pas un
service du réseau. Le `--host 0.0.0.0` ne concerne que l'intérieur du
conteneur, sans quoi la publication ne joindrait rien.

**Son tag suit `latest`, délibérément** — et c'est l'inverse du réflexe.
`music_assistant/providers/ytmusic/__init__.py` installe le client
`bgutil-ytdlp-pot-provider` **sans version**, à chaque démarrage du
fournisseur, et le commentaire du code dit pourquoi : *« Google breaks things
quite often which requires us to update some packages very frequently.
Installing them dynamically prevents us from having to update MA. »* Le client
suit donc `latest`. Épingler le **serveur** garantirait la dérive entre les
deux, alors que bgutil exige qu'ils s'accordent : c'est le pin qui casse, pas
`latest`. La documentation de MA, qui réclame encore la version 1.2.1, est en
retard sur son propre code — la dernière image publiée est `1.3.2`
(2026-08-21).

MA **vérifie l'accessibilité de cette URL au démarrage** du fournisseur et
lève `LoginFailed` si elle ne répond pas : le conteneur n'est pas optionnel.
`DEFAULT_PO_TOKEN_SERVER_URL` vaut exactement `http://127.0.0.1:4416`, ce que
la publication sur la boucle locale sert sans réglage supplémentaire.

### Ce qui ne change pas

`hooks/install.py` et `hooks/activate.py` ne demandent **aucune
modification** :

- `music_assistant/` est de la donnée que `copytree` ne touche pas — rien de ce
  nom n'existe sous `stack/`, donc rien à ajouter à `PRESERVED` ;
- `activate.py` démarre déjà tout service déclaré sans conteneur, dans l'ordre
  rendu par `compose config --services`, broker en tête. Les deux nouveaux
  services en héritent sans qu'on écrive une ligne.

### La dépose sur cette machine

`hooks/install.py --root /` **ne sera pas lancé ici**. Cette machine est à la
fois la production et le poste de développement : son `.env` porte
`COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml`, et la règle 3 du hook
supprimerait la surcouche de développement en cours d'usage — puis
`scrub_dev_overlay()` la retirerait du `COMPOSE_FILE`. Le compose est donc
déposé à la main dans `/opt/nivuus/home-manager/`, le `.env` laissé intact.

## § 2 — Nommage des lecteurs

L'intégration `music_assistant` de Home Assistant crée **une entité par lecteur
MA**. Comme les lecteurs MA proviennent ici d'entités HA existantes, laisser MA
nommer ses lecteurs d'après elles produirait des `_2`, `_3` imprévisibles —
exactement le piège que documente le commentaire « ATTENTION renommage » de
`configuration.yaml`, qui a déjà coûté les héros des trois tablettes.

Les lecteurs MA sont donc **nommés explicitement**, et leurs `entity_id` fixés
dans le registre :

| Lecteur MA | `entity_id` | Bâti sur |
|---|---|---|
| Musique Salon | `media_player.musique_salon` | `media_player.bar_de_son` |
| Musique Cuisine | `media_player.musique_cuisine` | `media_player.enceinte_cuisine` |
| Musique Chambre | `media_player.musique_chambre` | `media_player.enceinte_chambre` |
| Musique Salle de bain | `media_player.musique_salle_de_bain` | `media_player.google_home_salle_de_bain` |
| Musique Maison | `media_player.musique_maison` | groupe MA des quatre ci-dessus |

Le préfixe `musique_` n'est pas cosmétique : il rend impossible toute collision
avec les entités Cast sous-jacentes, qui restent en place et gardent leur rôle
(annonces TTS, `media_player.notification`).

**Conséquence assumée de la contrainte « uniquement les providers HA »** : le
groupe « Musique Maison » est synchronisé par MA sur quatre flux HTTP
indépendants, et non par la synchronisation Cast native. Un léger décalage
entre pièces est possible. C'est le prix de ne pas dépendre du provider
Chromecast de MA ni de l'application Google Home.

## § 3 — Configuration reprise de l'ancienne intégration

| Ancien | Nouveau |
|---|---|
| OAuth TV | **cookie `__Secure-3PAPISID` + PO token** |
| `speakers` (6, dont 3 mortes) | les 4 lecteurs MA + le groupe |
| `select_source` pour choisir l'enceinte | un lecteur par pièce, plus de sélecteur |
| `api_language: fr` | langue MA `fr` |
| `shuffle: true`, `Shuffle Random` | lecture aléatoire par défaut |
| `track_limit: 25` | limite de file MA |
| playlist « Mon supermix » `PLl-J6k2oaq_ef7_jQ8xXFH4KJQpXmY_A7` | reprise dans le script, en URI YouTube Music |

**Deux étapes ne peuvent pas être automatisées** et attendent l'opérateur :

1. l'extraction du cookie YouTube Music depuis une fenêtre de navigation
   privée — MA n'accepte pas l'OAuth de l'ancienne intégration, et le cookie
   expire périodiquement ;
2. l'autorisation OAuth Last.fm dans un navigateur.

## § 4 — Plugins

Dans cet ordre, qui est celui de leurs dépendances :

1. **Home Assistant** — d'abord, car il est prérequis : c'est lui qui donne à
   MA le provider *Home Assistant Media Players* **et** les moteurs IA et TTS
   de HA. Demande l'URL de HA et un jeton longue durée.
2. **AI Radio** — refuse de dépasser son premier écran sans un moteur IA et un
   moteur TTS. Ils viendront du plugin ci-dessus :
   `conversation.gemini_flash` et `tts.google_ai_tts`, tous deux déjà éprouvés
   dans HA. Ville renseignée pour les bulletins météo.
3. **Party** — file d'attente ouverte aux invités par QR code, une instance par
   lecteur.
4. **Music Quiz** — jeu multijoueur sur les enceintes.
5. **LastFM Scrobbler** — dernier, il dépend d'une autorisation navigateur.

## § 5 — Ordre d'exécution

**Non négociable** : Music Assistant installé, configuré, et **musique
effectivement validée sur les enceintes** avant tout retrait. YouTube Music
étant la seule source de la maison, l'ordre inverse laisse le logement sans
musique pendant toute la mise au point.

1. déposer les deux services, démarrer, vérifier l'UI sur 8095 ;
2. plugin Home Assistant, puis provider *HA Media Players* ;
3. fournisseur YouTube Music (cookie + PO token), vérifier une lecture ;
4. créer les 4 lecteurs + le groupe, fixer les `entity_id` ;
5. intégration `music_assistant` côté HA, vérifier les entités ;
6. **point de validation : la musique sort des quatre enceintes** ;
7. plugins AI Radio, Party, Music Quiz, LastFM ;
8. seulement alors : ménage du registre, retrait de `ytube_music_player`,
   bascule des références.

## § 6 — Ménage du registre

Sans référence ailleurs que dans le registre — vérifié sur `automations.yaml`,
`scripts.yaml`, `configuration.yaml`, `scenes.yaml`, `wallpanel.jinja`,
`wallpanel.js`, les trois dashboards, `homeassistant.exposed_entities`,
`config/packages/` et les sources du wallpanel, y compris par `device_id` :

- **supprimer** `media_player.enceinte_bureau`, `media_player.enceinte_bureau_2`
  (groupe Cast), `media_player.enceinte_chambre` (« Enceinte Droite ») ;
- **supprimer** les 11 entités `playfi2device*` autres que `2379390371` ;
- **réactiver** `media_player.playfi2device2379390371`, la renommer
  `media_player.bar_de_son` et la ranger dans la zone *Salon* ;
- **renommer** `media_player.bureau` → `media_player.enceinte_chambre`, une
  fois l'ancienne supprimée, et la ranger dans la zone *Chambre*.

Ces quatre opérations sont bon marché parce que le comptage l'a montré : zéro
occurrence hors registre. `media_player.maison`, lui, en compte **17** — c'est
la source « Multiroom » de partout, et il suit au § 7.

## § 7 — Retrait de `ytube_music_player` et bascule

Retrait : entrée de configuration, `config/custom_components/ytube_music_player/`,
dépôt HACS, `config/.storage/header_ytube_music_player.json`.

Bascule des six points d'ancrage, **sauvegarde datée avant chaque écriture**
selon la convention visible du dépôt (`<fichier>.backup-<sujet>-<date>`) :

| # | Fichier | Changement |
|---|---|---|
| 1 | `scripts.yaml` | `lancer_supermix_maison` → `music_assistant.play_media` sur `musique_maison` ; le `select_source` disparaît |
| 2 | `configuration.yaml` | déclencheur *Wallpanel héros* : `ytube_music_player` et `maison` → `musique_*` |
| 3 | `custom_templates/wallpanel.jinja` | macro `media_en_cours()`, idem |
| 4 | `tools/wallpanel-app/src/pieces.ts` | 3 pièces × 6 champs, puis **rebuild** vers `www/wallpanel/wallpanel.js` |
| 5 | `tools/wallpanel/rooms.py` | puis **régénération** de `genere/*.yaml` et réimport des `.storage/lovelace.wallpanel_*` |
| 6 | dashboards | `mini-media-player` : `source: "icon"` retiré — il servait le sélecteur d'enceinte de l'ancienne intégration, que MA n'a pas |

Mapping final du wallpanel :

| Tablette | source « Musique » | source « Multiroom » |
|---|---|---|
| Salon | `musique_salon` (barre de son) | `musique_maison` |
| Cuisine | `musique_cuisine` | `musique_maison` |
| Bureau | `musique_salon` (barre de son) | `musique_maison` |

Les points 4 et 5 vivent dans `/opt/nivuus/HomeAssistant/data/tools/`, **hors
du dépôt** : ils sont modifiés à la source, sans quoi le prochain build
réintroduirait `ytube`.

## § 8 — Tests et documentation

- `tests/test_compose_portable.py` doit rester vert : les volumes des deux
  nouveaux services sont relatifs.
- **Assertion ajoutée** : `bgutil-pot-provider` ne publie son port que sur
  `127.0.0.1` — un `4416` exposé au LAN offrirait à quiconque un générateur de
  jetons YouTube tournant sous l'identité de la maison.
- `README.md` : les deux services entrent dans le tableau.
- `CLAUDE.md`, « Décisions à ne pas défaire » : Music Assistant appartient au
  package (même raisonnement que mosquitto) ; le tag du PO provider est épinglé
  et pourquoi ; les lecteurs MA sont préfixés `musique_` et pourquoi.

## Ce que ce design ne fait pas

- il n'ajoute **aucune bibliothèque musicale locale** — la question ne se pose
  pas tant que Plex n'a pas de section musique ;
- il ne touche pas au groupe Cast « Maison », laissé cassé : plus rien n'en
  dépendra une fois le § 7 passé, et le réparer demanderait l'application
  Google Home ;
- il ne renomme pas les entités Cast sous-jacentes autres que les deux du § 6 ;
- il n'installe pas les plugins `Spotify Connect`, `Plex Connect` ni
  `Library Recommendations`, hors demande.
