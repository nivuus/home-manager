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

## Style

Scripts de test autonomes lancés par `make test`, pas de pytest, pas de
dépendance hors `python3` + PyYAML — c'est le style du dépôt `installer`.
