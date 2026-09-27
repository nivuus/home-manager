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
- **`matterjs-server`** (image `ghcr.io/matter-js/matterjs-server`) remplace
  `matter-server` (`python-matter-server`) depuis juillet 2026. Ne pas
  confondre : le second n'existe plus que comme conteneur arrêté sur l'hôte de
  référence.
- **`zigbee2mqtt/configuration.yaml` n'est jamais réécrit après le premier
  passage.** zigbee2mqtt y range le `network_key`, le `pan_id` et
  l'`ext_pan_id` du réseau qu'il forme. Les remplacer n'affiche aucune erreur :
  cela forme un *autre* réseau, sur lequel aucun des équipements appariés ne se
  trouve. C'est la raison d'être de `RENDERED` dans `hooks/install.py` — le
  gabarit n'est rendu que sur un fichier que la copie vient de créer.
- **Le txpower Thread est réappliqué en boucle** dans le `command` du service
  `otbr`. `otbr-agent` ne le persiste pas : retirer la boucle, c'est revenir
  silencieusement à 0 dBm et perdre les enfants Thread lointains.
- **`OTBR_RCP_ADDITIONAL_ARGS` doit rester vide.** Le défaut de l'image
  (`&uart-flow-control`) bloque la transmission vers l'EFR32 du SLZB, dont le
  contrôle de flux matériel est désactivé : la réception fonctionne, les
  commandes ne parviennent jamais.
- **`per_listener_settings true`** dans `mosquitto.conf` : sans cette
  directive, l'`allow_anonymous` du listener 8883 (requis par les ampoules
  Meross) deviendrait global et ouvrirait aussi 1883 et 1884.
- **Le watchdog OTBR reste dehors**, désactivé en production le 2026-05-04 :
  213 redémarrages de passerelle par semaine pour zéro récupération.

## personas_home : trois canaux, et pourquoi le code est ici

L'intégration `personas_home` vit dans `stack/config/custom_components/`. C'est
le **seul** contenu de `config/` que ce dépôt versionne : tout le reste de ce
répertoire porte de la donnée et relève du propriétaire de la machine (règle 1
de `hooks/install.py`), alors que ceci est du code.

**Trois canaux, trois correspondants, chacun avec son URL, sa persona et son
jeton.** Ils n'en faisaient qu'un jusqu'au 2026-09-06 :

| Canal | Sert | Destination au 2026-09-06 |
|---|---|---|
| `url` | entité `conversation` (**synchrone**) | studio personas, persona Hestia |
| `event_url` | `personas_home.send_event` | webhook de Bleuenn |
| `feedback_url` | `personas_home.send_feedback` | webhook de Bleuenn |

**Pourquoi la conversation n'a pas suivi les autres chez Bleuenn.** Elle est le
seul canal synchrone : elle poste et *attend* la réponse. Quatre appelants en
dépendent — `scripts.yaml:837` et trois automations, dont le résumé Hestia qui
**parse le JSON renvoyé**. Le webhook de Bleuenn répond 202 sans corps : y
repointer ce canal aurait rendu ces quatre appels muets sans une seule erreur.

**Deux formes d'URL, lues sur l'URL elle-même** (`payloads.webhook_endpoint`) :
sans chemin (`http://127.0.0.1:8080`) c'est une base studio et la persona lui
est ajoutée ; avec un chemin (`https://iris…/h/<secret>`) c'est déjà l'endpoint
complet, utilisé verbatim — le secret EST le chemin, et lui ajouter quoi que ce
soit donne 404. Sur un endpoint complet la persona ne nomme plus rien : un
`persona:` passé par une automation est **journalisé comme ignoré**, pas
silencieusement jeté.

**Le jeton est par canal, et facultatif.** Les webhooks de Bleuenn portent leur
secret dans le chemin ; leur envoyer le jeton du studio le divulguerait à un
hôte qui n'a rien à en faire. Un jeton vide = aucun en-tête `Authorization`, et
un canal n'hérite jamais du jeton d'un autre.

**Ce que la sonde du formulaire refuse, et ce qu'elle laisse passer.** C'est le
seul endroit où une erreur d'URL est encore rattrapable, puisque les deux
services sont fire-and-forget. Elle distingue trois échecs, et l'**ordre des
`except` EST le comportement** (les trois sont des sous-classes de
`ClientConnectorError`) :

- l'hôte répond 404/405/401 → **refusé** : c'est la confusion base/endpoint ;
- **TLS échoue → refusé.** Ce n'est pas hypothétique : un nom `*.ts.net`
  inventé résout par un joker et échoue *ici*, pas sur le DNS. Classé après
  `ClientConnectorError`, il passait pour « simplement éteint » — c'est
  arrivé le 2026-09-06, et la sonde acceptait un hôte bidon ;
- DNS introuvable → refusé ;
- connexion refusée ou expirée → **accepté, avec une ligne de journal**. Le
  studio était éteint le jour de la configuration : refuser là-dessus rendait
  une configuration légitime impossible à enregistrer, et la panne est bruyante
  à l'exécution (un WARNING par appel).

**Les trois payloads sont dans `payloads.py`, qui n'importe pas Home
Assistant.** Délibéré : les deux services sont fire-and-forget, donc une forme
qui dérive ne produit aucune erreur nulle part. `make test` (python3 seul) est
le seul endroit où ce contrat peut encore échouer. `details` porte une
**chaîne** sur le canal événements et un **objet** sur le canal feedback : d'où
deux services et non un service à branches.

**`OWNED_TREES` dans `hooks/install.py`.** La copie de `stack/` fusionne ; un
module retiré entre deux versions, ou un `__pycache__` périmé, survivrait à la
mise à jour et Home Assistant le chargerait. Ce répertoire est donc supprimé
puis recopié — la règle 2 de `home-stock`, apprise là-bas. La liste ne nomme
que ce que ce paquet dépose : emporter les voisins de `custom_components/`
désinstallerait le garde-manger en mettant à jour le socle.

**Ce qui reste invisible.** La bascule des deux appels de
`config/automations.yaml` (automation `bleuenn_relay_persistent_notif`), le
retrait du bloc `rest_command: ha_ai_feedback` de `config/configuration.yaml`
et les URLs de production (dans `.storage`, jamais dans le dépôt) ont eu lieu
**en place, sans commit**. Cette section est la seule trace qu'ils ont changé
le 2026-09-06.

## Dette : la génération 1 des tablettes murales

Trois pièces **ont l'air vivantes** et ne le sont plus depuis le 2026-08-02 :

- `config/custom_templates/wallpanel.jinja` (163 lignes, 9 macros) ;
- le bloc `template:` de `config/configuration.yaml` (vers la ligne 194), qui
  déclare cinq capteurs `sensor.wallpanel_*` — dont un bloc `trigger:` à
  **16 entités déclencheuses** ;
- les trois dashboards `config/.storage/lovelace.wallpanel_{salon,bureau,cuisine}`.

Mesuré le 2026-09-05 : aucune ligne de l'application des tablettes (package
`home-desk`) ne lit ces capteurs. Les deux premiers portent désormais un
commentaire daté sur place.

**Pourquoi ce n'est pas fait.** Le retrait coûte une écriture manuelle dans
`configuration.yaml` — que ce package protège par son `PRESERVED` et que
`home-stock` a explicitement refusé de s'autoriser — la perte du filet de
retour arrière du 2026-08-02, et la disparition de cinq entités du registre
dont le renommage manuel `hero` / `heros` ne vit **que dans l'entity registry**
et n'est pas reproductible.

**Ce que ce n'est pas** : ce n'est pas une dette de `home-desk`. Un package ne
nettoie pas la maison de quelqu'un d'autre ; c'est de l'hygiène du socle.

**Reste dû, et bloqué.** L'archivage de
`/opt/nivuus/HomeAssistant/data/tools/wallpanel/` (le générateur Lovelace de
cette génération 1) est la Task 12 Steps 5-7 du plan `home-desk`. Elle est
**conditionnée à la clôture du chantier music-assistant**, qui portait encore
sept cases décochées au 2026-09-05 — dont sa propre Task 12 Step 7, « Vérifier
à l'œil sur une tablette ». `rooms.py` a été modifié le 2026-09-04 à 08:37 par
ce chantier : ce répertoire n'est pas de la matière dormante, c'est un chantier
d'autrui en cours.

## Style

Scripts de test autonomes lancés par `make test`, pas de pytest, pas de
dépendance hors `python3` + PyYAML — c'est le style du dépôt `installer`.
