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
