# Préparation jury : backend-iot-alerts (service de détection)

Questions qu'un jury peut poser sur le service `detection-service`, des plus simples aux plus
pointues. **Essaie d'abord de répondre seul**, puis vérifie avec les réponses en bas du document
(même numérotation).

---

## Partie 1 : les questions

### A. Vue d'ensemble

1. En une phrase, à quoi sert ce service ?
2. Quelles sont ses entrées et ses sorties ?
3. Quels capteurs sont utilisés et que mesure chacun ?
4. Quelles alertes le service sait-il détecter ?
5. Pourquoi avoir utilisé MQTT entre l'ESP et le serveur plutôt que HTTP ?
6. Le service fait plusieurs choses en même temps (MQTT, inférence, API, base, envoi). Comment, en Python ?
7. Que se passe-t-il quand on lance `detection-service` ? Décris le démarrage.
8. Comment le service s'arrête-t-il proprement ?
9. Où est la configuration et comment la change-t-on ?

### B. Réception MQTT et validation

10. Comment le service sait-il de quel appareil vient un message ?
11. Que se passe-t-il si un message est mal formé (JSON cassé, champ manquant, valeur hors plage) ?
12. Pourquoi horodater les mesures à la réception plutôt que d'utiliser le `ts` envoyé par l'ESP ?
13. Que se passe-t-il si le broker MQTT tombe ?
14. Pourquoi QoS 0 pour les mesures et QoS 1 pour les résultats et les commandes ?
15. Comment la connexion MQTT est-elle sécurisée ?
16. Le champ `gas_do` vaut `0` quand il y a du gaz. Pourquoi, et comment le gérez-vous ?
17. Comment combinez-vous la caméra et l'ESP d'une même pièce ?

### C. Fenêtres glissantes et features

18. Qu'est-ce qu'une « feature » ici ? Cite-en quelques-unes.
19. Pourquoi deux fenêtres, une de 2 s et une de 60 s ?
20. L'inférence est déclenchée toutes les 0,5 s par une horloge, pas à chaque message. Pourquoi ?
21. Comment calculez-vous la pente du gaz ou de la température ?
22. Pourquoi `features.py` est-il présenté comme « la seule source de vérité » ?
23. Comment gérez-vous une valeur manquante (DHT22 qui ne répond pas) ?
24. Comment sont stockées les mesures récentes en mémoire ? Comment évitez-vous qu'elles grossissent sans fin ?
25. À quoi sert `n_samples_short` ?
26. Si la caméra ne publie rien pendant 3 s, `cam_ratio` vaut-il 0 ?

### D. Baseline du capteur de gaz

27. Pourquoi une « baseline » pour le MQ-2 ? Comment est-elle calculée ?
28. Pourquoi la baseline est-elle gelée pendant une alerte gaz ou feu ?
29. Pourquoi `alpha = 1 − exp(−dt / tau)` et pas un alpha fixe ?

### E. Modèles et machine learning

30. Pourquoi Orange Data Mining ?
31. Multi-label ou multi-classe : lequel avez-vous choisi et pourquoi ?
32. Quel algorithme utilisez-vous ?
33. D'où viennent les données d'entraînement ?
34. Comment enregistre-t-on une situation réelle pour l'entraînement ?
35. Pourquoi découper entraînement/test **par session** et pas aléatoirement par ligne ?
36. Pourquoi chaque modèle ne voit-il qu'une partie des colonnes ?
37. Quelles métriques utilisez-vous pour évaluer un modèle ?
38. Que se passe-t-il s'il n'y a aucun modèle dans `models/` ?
39. Pourquoi n'y a-t-il pas de predictor de repli à règles ?
40. Comment le service vérifie-t-il qu'un modèle déposé est compatible ?
41. Pourquoi épingler les versions d'`orange3` et de `scikit-learn` dans le Dockerfile ?
42. Un `.pkcls` est un pickle. Est-ce un risque de sécurité ?
43. Comment changer de modèle sans redémarrer le service ? Et si le nouveau est cassé ?
44. Comment sait-on quel modèle est en production ?

### F. Post-traitement

45. Pourquoi ne pas déclencher l'alerte dès que la probabilité dépasse 0,5 ?
46. Explique le lissage exponentiel.
47. Explique l'hystérésis K/M, avec un exemple chiffré.
48. Pourquoi la présence demande-t-elle 8 ticks au lieu de 3 ?
49. Qu'est-ce que le « filet de sécurité gaz » et pourquoi est-il indépendant du modèle ?
50. Que se passe-t-il si le modèle lève une exception pendant une prédiction ?
51. Plusieurs alertes peuvent être actives en même temps. Comment choisir le statut global ?

### G. États d'un appareil

52. Quels sont les états possibles d'un appareil et quand passe-t-on de l'un à l'autre ?
53. Pourquoi un préchauffage de 120 s ?
54. En `no_data` ou `stale`, pourquoi garder les alertes figées au lieu de les annuler ?

### H. Sorties : résultats, alarme, commandes

55. Quand un résultat est-il publié ?
56. À quoi sert le heartbeat ?
57. Que se passe-t-il si la file d'envoi est pleine ?
58. Qu'est-ce que le backoff exponentiel, et pourquoi ajouter du « jitter » ?
59. Quels codes HTTP sont réessayés, lesquels sont abandonnés, et pourquoi ?
60. Comment l'alarme de l'ESP (buzzer, LED, écran) est-elle déclenchée et arrêtée ?
61. Comment fonctionne une commande du dashboard vers l'ESP (ex. afficher un message) ?
62. Comment une alerte arrive-t-elle en temps réel sur le dashboard ?

### I. Base de données

63. Quelles tables le service écrit-il ?
64. Pourquoi TimescaleDB ?
65. Pourquoi écrire par lots plutôt qu'à chaque mesure ?
66. Que se passe-t-il si PostgreSQL est indisponible ?
67. Pourquoi le service ne crée-t-il pas ses tables lui-même ?
68. Quel volume de données par jour et par appareil ? Que prévoyez-vous ?

### J. API, sécurité, déploiement

69. Quelles routes expose l'API ?
70. Comment les routes sensibles sont-elles protégées ?
71. Pourquoi valider le `device_id` avec une regex dans les routes de commande ?
72. Comment le service est-il conteneurisé ? Quelles bonnes pratiques dans le Dockerfile ?
73. Comment testez-vous le service sans matériel ?

### K. Recul critique

74. Quelles sont les limites actuelles du service ?
75. Que faudrait-il changer pour surveiller 500 pièces au lieu d'une ?

---
---

## Partie 2 : les réponses

### A. Vue d'ensemble

**1.** Il reçoit en temps réel les mesures d'un ESP8266 (mouvement, gaz, température,
humidité) et d'une caméra, calcule des indicateurs sur des fenêtres glissantes, les passe à un
modèle de machine learning (entraîné dans Orange) et déclenche des alertes `feu`, `fuite_gaz`,
`inondation` ou `presence` : en base, vers le dashboard et sur l'alarme de l'ESP.

**2.**
- **Entrées** (MQTT) : `sentinelx/{device_id}/telemetry` (mesures ESP, ~5 msg/s),
  `.../camera` (personne détectée), `.../alert` (alertes brutes de l'ESP), `.../ack`
  (acquittements des commandes).
- **Sorties** : `sentinelx/{device_id}/detection` (résultat, QoS 1), `.../cmd` (commandes vers
  l'ESP), la base PostgreSQL (schéma `detection` + table `public.alerts`), et une API HTTP
  FastAPI sur le port 8000.

**3.**
- **HW-416 (PIR)** : mouvement infrarouge, 0/1.
- **MQ-2** : gaz et fumée. Sortie analogique AO (ADC 0–1023) et sortie numérique DO
  (seuil réglé par potentiomètre).
- **DHT22** : température (°C) et humidité relative (%), au plus une lecture toutes les 2 s.
- **Caméra** : détection de personne (et reconnaissance faciale, enregistrée mais hors modèle).

**4.** `feu`, `fuite_gaz`, `inondation`, `presence`. Ordre de priorité :
feu > fuite_gaz > inondation > presence > aucune (`config.py`, `ALERT_TYPES`).

**5.** MQTT est un protocole publish/subscribe léger, conçu pour l'IoT : en-têtes minuscules,
connexion persistante (pas de handshake HTTP à chaque mesure, important à 5 msg/s sur un
ESP8266), découplage producteur/consommateur via le broker (Mosquitto), niveaux de QoS, et
facilité d'ajouter un abonné. HTTP n'est utilisé que pour l'API d'administration.

**6.** Avec **asyncio** (un seul thread, boucle d'événements). `service.py` lance des tâches
concurrentes : `mqtt` (réception), `ticker` (inférence toutes les 0,5 s), `api` (uvicorn),
`esp-alarm` (commandes d'alarme), `db-writer` (écriture par lots), `backend-sender` (publication
des résultats), plus `db-bootstrap` (connexion à la base avec reprises). Le moteur d'inférence
(`engine.py`) est **synchrone et sans I/O** : il pousse dans des files, ce qui évite de
bloquer la boucle et le rend testable avec une horloge simulée.

**7.** `main.run()` → lit la config (`Settings()`), configure les logs, construit `Service` :
stockage, `BatchWriter`, émetteur (MQTT ou HTTP), `CommandSender`, `EspAlarm`, predictor
(`create_predictor`), `DetectionEngine`, `MessageDispatcher`. Si le modèle est absent ou invalide,
`ModelLoadError` → arrêt avec code 2 et un message clair. Puis `asyncio.run(service.run())`
installe les signaux et lance les tâches.

**8.** SIGINT/SIGTERM déclenchent `request_stop()`. L'ordre compte : on arrête ticker, API et
alarme ; puis on **vide la file des résultats** (`sender.drain()`) **avant** de couper MQTT
(sinon les derniers résultats ne pourraient plus partir) ; puis on vide l'écrivain de base
(`writer.drain()`, borné à 10 s) et on ferme la connexion.

**9.** `config.py`, avec **pydantic-settings** : chaque champ de `Settings` est lu dans les
variables d'environnement ou dans `.env` (l'environnement est prioritaire). Les types et bornes
sont validés au démarrage (ex. `smoothing_alpha` entre 0 et 1). Tout est documenté dans
`.env.example`. Dans la pile, c'est le `docker-compose.yml` du dépôt `main` qui fixe ces variables.

### B. Réception MQTT et validation

**10.** Par le **topic**. `TopicPattern` (`mqtt_client.py`) transforme
`sentinelx/{device_id}/telemetry` en filtre MQTT `sentinelx/+/telemetry` (`+` = joker d'un
niveau) pour s'abonner, et en regex `^sentinelx/([^/]+)/telemetry$` pour extraire le
`device_id` du topic reçu.

**11.** `MessageDispatcher.dispatch()` valide avec **Pydantic** (`SensorMessage`,
`CameraMessage`…). En cas d'erreur (JSON invalide, `ValidationError`, topic inconnu, ou même
exception d'un traitement en aval), le message est **journalisé, compté
(`invalid_by_reason`) et ignoré** : la méthode ne lève jamais, donc un message pourri ne peut
pas tuer la boucle MQTT. Nuance : une température hors plage (−40 → 80 °C) ne rejette pas le
message, elle est juste remplacée par `null` (lecture DHT22 ratée). En revanche `gas_raw`
hors 0–1023 ou `pir` absent → message rejeté. Les compteurs sont visibles dans `/health`.

**12.** L'ESP8266 n'a pas d'horloge fiable (pas de RTC, NTP pas garanti). Toutes les mesures
d'un appareil sont horodatées par la **même horloge** (celle du serveur), donc les fenêtres et
les pentes sont cohérentes entre ESP et caméra. Le `ts` de l'ESP est conservé en base
(`device_ts`) à titre informatif. Contrepartie : la latence réseau s'ajoute à l'horodatage.

**13.** `run_mqtt()` boucle : en cas de `MqttError`, il attend puis se reconnecte avec un
**backoff exponentiel** (1 s, 2 s, 4 s… jusqu'à 30 s), et refait les abonnements à chaque
connexion. Les publications de résultats attendent la reconnexion (`_online` event). Pendant
la coupure, plus de mesures → les appareils passent en `stale`.

**14.**
- **QoS 0** (« au plus une fois ») pour les mesures : 5 msg/s, perdre une mesure n'a pas
  d'importance, la suivante arrive 200 ms après. Moins de trafic et de charge sur l'ESP.
- **QoS 1** (« au moins une fois ») pour les résultats et les commandes : un changement
  d'état ou un ordre d'alarme ne doit pas se perdre. Les doublons possibles sont gérés
  (commandes identifiées par un `id`, résultats horodatés).

**15.** Dans la pile complète : **MQTTS** (TLS, port 8883) avec vérification du certificat de
l'autorité (`ca.crt`), **comptes par rôle** (`sentinel_iot` pour l'ESP, `vision` pour la
caméra, `detection` pour ce service) et **ACL** Mosquitto qui limitent qui peut lire/écrire
quel topic. `MQTT_TLS_INSECURE` existe pour le dev uniquement.

**16.** La sortie DO du MQ-2 est **active basse** (le comparateur tire la ligne à 0 quand le
seuil est dépassé). L'ESP envoie la valeur brute, sans transformation, et le service la
normalise une seule fois : `SensorMessage.gas_alert` renvoie `True` si `gas_do == 0`. Ensuite,
partout dans le code, `gas_do = True` veut dire « alerte ».

**17.** Par le `device_id` : la caméra d'une pièce publie avec **le même `device_id`** que
l'ESP. Les deux flux arrivent dans le même `DevicePipeline` (un par appareil, créé à la
première réception) et sont combinés dans les features (`pir_ratio` et `cam_ratio`).

### C. Fenêtres glissantes et features

**18.** Une feature est un nombre qui résume une fenêtre de mesures, et que le modèle prend
en entrée. Il y en a 14 (`FEATURE_NAMES`) : `pir_ratio`, `cam_ratio` (proportion de 1),
`gas_mean/max/min`, `gas_slope`, `gas_delta_baseline` (écart à la normale), `gas_do_ratio`,
`temp_last`, `hum_last`, `temp_delta_long`, `hum_delta_long`, `temp_slope_long` (°C/min),
`n_samples_short`.

**19.** Les phénomènes n'ont pas la même vitesse. Mouvement et gaz réagissent en quelques
secondes : fenêtre **courte de 2 s** pour être réactif. Température et humidité varient
lentement, et le DHT22 ne donne qu'une lecture toutes les 2 s : il faut **60 s** pour voir une
tendance (un feu fait monter la température de plusieurs °C/min, une inondation fait monter
l'humidité).

**20.**
- Fréquence de calcul **constante** (2 Hz), quel que soit le débit des messages : charge CPU
  prévisible, et les modèles voient des fenêtres régulières.
- On **détecte l'absence** de messages : si on ne calculait qu'à la réception, un capteur muet
  ne déclencherait jamais le passage en `stale`.
- Les ticks en retard ne sont pas rattrapés (`_ticker`), pour éviter une rafale d'inférences.

**21.** Par **régression linéaire aux moindres carrés** (`linear_slope`) :
`pente = Σ(t − t̄)(v − v̄) / Σ(t − t̄)²`. C'est plus robuste au bruit qu'un simple
(dernier − premier) / durée. Renvoie 0 avec moins de 2 points. La pente de température est
convertie en °C/min (× 60).

**22.** Pour éviter le **training-serving skew** : si les features calculées à l'entraînement
et en production diffèrent (même légèrement), le modèle se trompe en production. Ici, les
fenêtres calculées en temps réel par `compute_features` sont stockées telles quelles dans
`feature_windows`, puis exportées vers Orange. Le script `train_models.py` passe lui aussi par
le vrai `DevicePipeline`. La fonction est **pure** (pas d'état, pas d'I/O) : même entrée,
même sortie, facile à tester.

**23.** Une valeur manquante vaut `NaN` dans les features (Orange sait l'**imputer**), `null`
en JSON et en base (`nan_to_none`). `temp_last` / `hum_last` deviennent manquants si la
dernière lecture a plus de `DHT_MAX_AGE_S` (30 s). L'ESP doit envoyer `null` plutôt que
répéter l'ancienne valeur, sinon on masquerait une panne.

**24.** `buffers.py` : une `deque` de paires `(horodatage, valeur)` par signal et par appareil.
Ajout en O(1) à droite ; à chaque tick, `prune()` retire à gauche ce qui est trop vieux :
2 s pour PIR et gaz, max(60 s, 30 s) pour température et humidité, 2 + 5 s pour la caméra.
La mémoire est donc bornée par la durée des fenêtres.

**25.** C'est le nombre de mesures dans la fenêtre courte. Sous `MIN_SAMPLES_SHORT` (5),
l'appareil passe en `no_data` : on ne prédit pas sur trop peu de points (un ESP qui envoie
mal donnerait des ratios ou pentes absurdes).

**26.** Non, pas forcément. `_camera_ratio` : si aucun message caméra dans la fenêtre de 2 s,
on reprend la **dernière valeur** si elle date de moins de `CAMERA_HOLD_S` (5 s). Au-delà,
0. Cela tolère une caméra qui publie à 1 msg/s ou un peu moins.

### D. Baseline du capteur de gaz

**27.** Le MQ-2 **dérive** : sa valeur au repos dépend de la température, de l'humidité, de
son vieillissement et de la pièce. Un seuil absolu (ex. « > 300 ») serait faux d'un capteur à
l'autre. On compare donc à une référence : `gas_delta_baseline = gas_mean − baseline`.
Calcul (`baseline.py`) : **médiane** des 10 premières moyennes après le préchauffage (la
médiane résiste à une valeur aberrante), puis **moyenne mobile exponentielle** de constante de
temps 600 s (elle suit lentement la dérive).

**28.** Sinon, pendant une fuite qui dure, la baseline monterait vers la valeur de la fuite, le
delta retomberait à 0 et l'alerte s'éteindrait alors que le gaz est toujours là : la fuite
deviendrait « la normale ». Le temps continue d'avancer pendant le gel, pour éviter un
rattrapage brutal à la reprise.

**29.** Avec un alpha fixe, la vitesse d'adaptation dépendrait de la fréquence des ticks
(changer `INFERENCE_INTERVAL_S` changerait le comportement). Avec `1 − exp(−dt/τ)`, la baseline
se comporte comme un filtre passe-bas de constante de temps τ, **quel que soit `dt`** : après
τ = 600 s, elle a parcouru ~63 % de l'écart.

### E. Modèles et machine learning

**30.** Contrainte/choix du projet : Orange est un outil visuel (widgets), adapté pour
explorer les données, comparer des algorithmes et évaluer sans écrire de code d'entraînement.
Il exporte les modèles en `.pkcls` (widget *Save Model*), que le service charge avec la
bibliothèque `orange3`. L'entraînement est dans Orange, le service ne fait que l'inférence.

**31.** **Multi-label** par défaut (`MODEL_MODE=multilabel`) : un modèle binaire par alerte
(`feu.pkcls`, `fuite_gaz.pkcls`…). Raisons : les alertes **ne sont pas exclusives** (une
personne présente pendant une fuite de gaz), chaque modèle peut être réentraîné ou remplacé
indépendamment, et un type sans modèle vaut simplement 0. Le mode multi-classe (un seul
`model.pkcls`) reste possible mais ne prédit qu'une classe à la fois.

**32.** Une **forêt aléatoire** (`RandomForestLearner` d'Orange, basé sur scikit-learn) :
60 arbres, profondeur max 14, au moins 8 exemples par feuille, `class_weight="balanced"` (les
alertes sont rares par rapport à « aucune »). Avantages : robuste, peu de réglages, pas besoin
de normaliser les features, donne des probabilités.

**33.** Les modèles livrés sont entraînés par `tools/train_models.py` sur :
- des **sessions simulées** (`simulation.py`) : normal → situation → normal, avec des
  **pièges** dans les sessions « aucune » (bouffée de gaz, chauffage, humidité, PIR parasite) ;
- des **fenêtres réelles « aucune »** enregistrées dans la pièce (`exports/temoins_aucune/`),
  pour limiter les faux positifs en conditions réelles.

Les fenêtres sont calculées avec le vrai pipeline. Le début d'une situation n'est pas étiqueté
(temps de réaction des capteurs), ni la phase de retour au calme. À terme, ils doivent être
remplacés par des modèles entraînés sur des sessions réelles enregistrées.

**34.** Via l'API : `POST /recording/start` avec `{"label": "fuite_gaz", "device_id": "esp01",
"notes": "..."}`, on reproduit la situation 1 à 3 min, puis `POST /recording/stop`. Pendant la
session, chaque fenêtre est enregistrée dans `feature_windows` avec `session_id` et `label`.
Ensuite `tools/export_dataset.py --target feu --orange-flags` produit un CSV qu'Orange
reconnaît directement (`cD#label` = cible discrète, `mS#session_id` = méta).

**35.** Deux fenêtres consécutives d'une même session (0,5 s d'écart, 2 s de fenêtre) sont
presque identiques. Avec un découpage aléatoire par ligne, le modèle verrait en test des
quasi-copies de ses données d'entraînement : c'est une **fuite de données**, le score serait
excellent mais trompeur. En découpant par session, on mesure la capacité à généraliser à une
**nouvelle situation**. C'est aussi pour ça qu'on ne garde qu'une fenêtre sur 4.

**36.** Pour qu'un modèle n'apprenne pas une **corrélation fortuite** des données simulées
(ex. le modèle présence qui utiliserait l'humidité). Chaque modèle ne voit que les signaux
physiquement liés : présence → `pir_ratio`, `cam_ratio` ; inondation → humidité ;
fuite de gaz → gaz + température (pour distinguer d'un feu) ; feu → gaz + température + humidité.

**37.** Dans `train_models.py` : **AUC** (capacité à classer les positifs au-dessus des
négatifs, indépendamment du seuil), **rappel** au seuil 0,6 (proportion d'alertes réelles
détectées : le plus important pour la sécurité), et **taux de faux positifs** séparé sur les
négatifs simulés et sur les négatifs **réels**.

**38.** Le service refuse de démarrer : `create_predictor` lève `ModelLoadError`, avec un
message qui donne le dossier cherché et les fichiers attendus, et le processus s'arrête avec le
code 2. En mode multi-label, il suffit d'un seul fichier : un type sans modèle vaut 0. Les
quatre modèles de départ sont versionnés dans `models/`, le cas ne se produit donc pas en
fonctionnement normal.

**39.** Un repli silencieux masquerait un problème : le service aurait l'air de fonctionner
alors qu'il détecte avec de simples seuils et non avec le modèle entraîné. On préfère une erreur
explicite au démarrage. La seule sécurité indépendante du modèle est le filet gaz
(`GasSafetyRule`, question 49) : il reste actif même si le modèle plante en cours de route.

**40.** Au chargement (`predictors/base.py`, `orange.py`) :
- chaque colonne attendue par le modèle doit exister dans `FEATURE_NAMES`
  (`check_features`), sinon message qui explique quoi corriger dans Orange ;
- toutes les colonnes doivent être numériques, et le modèle doit être un classifieur ;
- en binaire, la **classe positive** est identifiée (`positive_index` : nom de l'alerte,
  ou « 1/true/oui », ou l'autre classe que « aucune ») ;
- les colonnes sont fournies dans **l'ordre exact** attendu par le modèle (`build_row`).

En cas d'erreur : arrêt au démarrage, ou conservation de l'ancien modèle lors d'un rechargement.

**41.** Un `.pkcls` est un **pickle** Python : il contient les objets internes d'Orange et
de scikit-learn. Les recharger avec une autre version peut échouer ou, pire, donner des
résultats différents en silence. L'image installe donc les mêmes versions que l'application
Orange qui produit les modèles (3.40.0 / scikit-learn 1.5.2, arguments `ORANGE_VERSION` et
`SKLEARN_VERSION`).

**42.** Oui : `pickle.load` peut **exécuter du code arbitraire**. Le risque est accepté parce
que les fichiers sont de confiance (produits par l'équipe, déposés dans un volume monté), et
c'est documenté dans le code (`# noqa: S301`). Il ne faut jamais charger un `.pkcls` venant
d'une source inconnue. Amélioration possible : vérifier une empreinte/signature, ou exporter
dans un format sans code (ONNX).

**43.** Trois moyens : `docker compose kill -s HUP detection-service` (signal SIGHUP),
`POST /admin/reload-model` (jeton admin), ou redémarrage. `reload_model()` relit la
configuration, construit le nouveau predictor **puis** le remplace. S'il est invalide
(`ModelLoadError`), **l'ancien reste en place** et l'erreur est visible dans `/health`
(`last_reload_error`) ; l'API renvoie 422.

**44.** `/health` → `model` : type de predictor, fichiers chargés, colonnes, classes et
`version`. La version vaut `orange-<8 premiers caractères du SHA-256 des fichiers>`
(`fingerprint`) : elle change automatiquement dès qu'un modèle est remplacé. Elle est aussi
envoyée dans chaque résultat (`model_version`) et dans chaque alerte en base : on sait quel
modèle a pris quelle décision.

### F. Post-traitement

**45.** Une prédiction brute toutes les 0,5 s est **bruitée** : une valeur isolée au-dessus du
seuil ferait clignoter l'alerte (on/off/on), déclencherait le buzzer pour rien et inonderait
le dashboard. On lisse donc, puis on exige une confirmation sur plusieurs ticks.

**46.** `lissé = lissé + α × (proba − lissé)`, avec α = 0,5 (`SMOOTHING_ALPHA`). Chaque
nouvelle valeur compte pour moitié, l'historique pour l'autre moitié : un pic isolé est
atténué. α = 1 désactive le lissage.

**47.** Deux seuils et deux compteurs (`AlertTracker`) :
- l'alerte **s'active** après K = 3 probabilités lissées **consécutives** ≥ 0,6 ;
- elle **se désactive** après M = 6 probabilités lissées **consécutives** < 0,4.

Avec un tick de 0,5 s : activation en ~1,5 s, désactivation après ~3 s de calme. Entre 0,4 et
0,6, rien ne change : c'est la zone d'hystérésis, qui évite le clignotement autour d'un seuil
unique. On désactive plus lentement qu'on active : mieux vaut une alerte qui dure un peu trop
qu'une alerte coupée à tort. Réglable par type avec `ALERT_OVERRIDES`.

**48.** Des mesures en pièce vide ont montré que le PIR produit des **impulsions parasites
isolées de 0,6 à 0,9 s**. Avec K = 3 (1,5 s) certaines déclenchaient l'alarme. Avec
K = 8 (~4 s, `DEFAULT_ALERT_OVERRIDES`), il faut une présence soutenue.

**49.** `GasSafetyRule` force `fuite_gaz` (avec `source: "rule"`, confiance 1,0) si
`gas_max ≥ 800` (ADC) ou si la sortie DO du MQ-2 est en alerte sur toute la fenêtre pendant
4 ticks. Il est indépendant du modèle parce qu'un modèle ML peut se tromper ou être mal
entraîné : pour un danger vital, une règle simple et déterministe garantit qu'une
concentration très élevée déclenche toujours l'alerte. Le champ `source` permet de savoir qui
a déclenché.

**50.** L'exception est attrapée dans `DevicePipeline.tick()`, comptée (`predict_errors`,
visible dans `/health`) et journalisée (la 1re fois puis toutes les 100 pour ne pas saturer
les logs). Les probabilités valent alors `{}` (0 partout), mais le **post-traitement continue**,
donc le filet de sécurité gaz reste actif. Une erreur dans un tick n'arrête pas non plus la
boucle (`_ticker` attrape tout).

**51.** Les quatre alertes sont calculées indépendamment (`alerts[]` dans le payload). Le
champ `status` donne **la plus prioritaire** des actives : feu > fuite_gaz > inondation >
presence > aucune (`global_status`). Même règle pour le texte affiché sur l'écran de l'ESP.

### G. États d'un appareil

**52.** (`DevicePipeline._state`)
- `no_data` : aucune mesure reçue, ou moins de 5 mesures dans la fenêtre de 2 s ;
- `stale` : plus aucun message depuis 10 s (`STALE_AFTER_S`), appareil probablement hors ligne ;
- `warming_up` : moins de 120 s depuis la première mesure, ou l'ESP envoie `warmup: true`, ou
  la baseline gaz n'est pas encore initialisée (10 ticks) ;
- `ok` : on prédit.

**53.** Le MQ-2 contient une **résistance chauffante** : à froid, sa valeur est fortement
décalée et descend pendant plusieurs dizaines de secondes. Prédire pendant ce temps donnerait
de fausses fuites de gaz, et la baseline serait faussée. Pendant `warming_up`, toutes les
alertes sont inactives. Configurable (`WARMUP_SECONDS=20` pour une démo).

**54.** Un capteur qui se tait **pendant un incendie** (brûlé, coupure de courant) ne veut pas
dire que le danger a disparu. On renvoie donc le dernier état connu, figé, avec `device_state`
pour que le dashboard affiche « capteur hors ligne, dernière alerte : feu » plutôt qu'annuler
l'alerte en silence.

### H. Sorties : résultats, alarme, commandes

**55.** (`DetectionEngine.tick`) À chaque **changement d'état** (`reason: "state_change"`) :
statut global, activation/désactivation d'une alerte, source, ou `device_state`. Sinon, un
**heartbeat** toutes les 10 s. Chaque envoi est aussi écrit dans `detection.predictions`,
que backend-api lit pour connaître l'état de chaque appareil.

**56.** À prouver que le service est **vivant** : sans aucun message pendant plus de
2 × 10 s, le backend sait que la détection est arrêtée. Il resynchronise aussi l'état courant
si un message de changement a été perdu, et sert au suivi « vu à ».

**57.** (`BackendSender.enqueue`, 500 messages max) La file ne bloque jamais. Un heartbeat
remplace le heartbeat du même appareil déjà en file. File pleine : on jette d'abord un
heartbeat, et seulement s'il n'y en a pas, le changement d'état le plus ancien (avec un log
d'erreur). Les changements d'état sont donc prioritaires, et le heartbeat suivant
resynchronise.

**58.** Après chaque échec, on attend de plus en plus longtemps (0,5 s, 1 s, 2 s… jusqu'à
10 s) pour ne pas marteler un serveur déjà en difficulté. Le **jitter** (délai multiplié par
un aléatoire entre 0,5 et 1) évite que plusieurs clients réessaient tous au même instant
(effet « troupeau »).

**59.** Réessayés : erreurs réseau, timeout, 408, 425, 429 et 5xx : des erreurs
**temporaires** (serveur surchargé ou indisponible). Abandonnés : les autres 4xx (400, 401,
404, 422…) : erreur de contrat ou de configuration, réessayer donnerait le même résultat.
Remarque : dans la pile actuelle, les résultats partent en MQTT, l'envoi HTTP n'est utilisé
que si `MQTT_RESULT_TOPIC` est vide.

**60.** À chaque tick, `_update_alarm` calcule l'alerte la plus prioritaire parmi
`ESP_ALARM_TYPES`. Si elle change, `EspAlarm.set()` met la demande en file (le moteur reste
synchrone). Un worker envoie `alert on` puis `screen message "INCENDIE DETECTE"` sur
`sentinelx/{id}/cmd`, et attend l'acquittement. Sans acquittement, il réessaie toutes les
5 s. L'arrêt (`alert off` + `screen auto`) n'a lieu qu'après **5 s sans alerte**
(`ESP_ALARM_OFF_DELAY_S`), pour ne pas couper la sirène entre deux alertes proches. L'ESP n'a
plus de seuil local : c'est le serveur qui décide.

**61.** Le dashboard appelle backend-api, qui appelle `POST /devices/{id}/screen` sur ce
service (jeton admin). `CommandSender.send()` génère un **UUID**, crée une `Future`, publie
`{"id": ..., "command": "screen", ...}` en QoS 1, et attend l'acquittement de **même id** sur
`.../ack` (5 s max). Réponse HTTP : 200 si OK, 422 si l'ESP refuse, 503 si le broker est
injoignable, 504 sans acquittement (ESP hors ligne). Un appareil ne peut acquitter que ses
propres commandes (on vérifie le `device_id`).

**62.** À l'**activation** d'une alerte (pas à chaque tick), le service insère une ligne
dans `public.alerts` ; ces lignes déclenchent une écriture immédiate (pas d'attente du lot) et
ne sont jamais jetées. Un **trigger PostgreSQL** (`03_notify.sql`) fait un `NOTIFY`,
backend-api l'écoute et diffuse l'alerte au dashboard en **WebSocket**.

### I. Base de données

**63.** Schéma `detection` : `sensor_readings` (mesures brutes), `camera_events`,
`camera_state` (dernier état par caméra), `feature_windows` (une colonne par feature +
`session_id`, `label`), `predictions` (résultats envoyés, alertes en JSONB),
`recording_sessions`. Plus la table commune `public.alerts`.

**64.** C'est une extension de PostgreSQL pour les **séries temporelles** : les hypertables
partitionnent automatiquement par temps (`sensor_readings`, `camera_events`,
`feature_windows`), les insertions et requêtes par plage de temps restent rapides, et on peut
définir une **politique de rétention** (supprimer les mesures de plus de 30 jours). On garde le
SQL standard et une seule base pour tout le projet.

**65.** 5 mesures/s + 2 fenêtres/s par appareil : une transaction par ligne serait très
coûteuse. `BatchWriter` empile en mémoire et écrit des lots de 200 lignes, au plus tard toutes
les secondes. Et surtout, `put()` ne bloque jamais : une base lente ne ralentit pas la
détection.

**66.** La **détection continue**. Au démarrage, `_db_bootstrap` réessaie la connexion avec
backoff (jusqu'à 30 s). En fonctionnement, un lot en échec est **remis en tête de file** et
réessayé avec backoff. La mémoire est bornée (`DB_MAX_BUFFERED_ROWS` = 50 000) : au-delà, on
jette d'abord les mesures brutes, puis les événements caméra, puis les fenêtres, puis les
prédictions ; jamais les sessions, les alertes ni l'état caméra. `/health` passe en `degraded`.

**67.** Le schéma de toute la base est défini **à un seul endroit**, le dépôt `database`
(`db/init/`), pour éviter que plusieurs services se marchent dessus ou que le schéma diverge.
`storage/tables.py` décrit les tables côté code (SQLAlchemy) et doit être tenu cohérent avec
`02_detection.sql`. `create_all()` n'existe que pour les tests avec SQLite.

**68.** Environ 5 × 86 400 ≈ **430 000 mesures** et 2 × 86 400 ≈ **170 000 fenêtres** par jour
et par appareil. Prévu : politique de rétention TimescaleDB sur les mesures brutes (ex.
30 jours), en **gardant les fenêtres étiquetées** (précieuses pour l'entraînement). L'option
`STORE_ALL_FEATURE_WINDOWS=false` permet de ne stocker les fenêtres que pendant les sessions
d'enregistrement.

### J. API, sécurité, déploiement

**69.** `GET /health` (santé détaillée), `GET /status/{device_id}` (dernier résultat),
`GET/POST /recording[/start|/stop]` (sessions étiquetées), `POST /admin/reload-model`,
`POST /devices/{id}/alert|buzzer|led|screen|reset` (commandes ESP). Documentation interactive
générée par FastAPI sur `/docs`.

**70.** Par un jeton **Bearer** (`ADMIN_TOKEN`), vérifié par la dépendance FastAPI
`require_admin`, sur le rechargement du modèle et les commandes ESP. Dans la pile, seul
backend-api l'appelle, sur le réseau Docker interne. Limites à connaître : si `ADMIN_TOKEN`
est vide il n'y a aucune protection, et les routes `/recording` ne sont pas protégées.

**71.** Le `device_id` est inséré dans un **topic MQTT**. Sans validation, un attaquant
pourrait passer `+` ou `#` (jokers MQTT) ou `/` (changer de niveau) et viser d'autres
topics. La regex `^[\w.-]{1,64}$` n'autorise que lettres, chiffres, `_`, `.`, `-` : c'est une
protection contre l'**injection**, comme une requête SQL paramétrée.

**72.** Dockerfile **multi-étapes** : l'étape `build` installe `g++` et compile les
dépendances dans `/opt/venv` ; l'image finale ne copie que le venv (image plus légère, pas de
compilateur en production). Les dépendances lourdes sont installées **avant** de copier le
code (cache Docker). Le service tourne avec un **utilisateur non root** (`app`, uid 1000).
Un `HEALTHCHECK` appelle `/health` toutes les 15 s. Les modèles sont montés en volume sur
`/models`.

**73.** Avec le **simulateur** (`tools/simulator.py`, `make sim`), qui publie de vrais messages
MQTT selon des scénarios enchaînés (`normal:150,fuite_gaz:45,feu:120,capteur_muet:20…`),
y compris des cas d'erreur (`dht_nan`, `capteur_muet`). Le moteur étant synchrone avec une
horloge injectable (`tick(now=...)`), il peut être testé sans attendre en temps réel.
Attention : le README mentionne `pytest` et un dossier `tests/`, mais ce dossier **n'est pas
présent dans le dépôt actuellement**. Si le jury demande les tests unitaires, il faut le
savoir (et idéalement les ajouter ou les retrouver avant la soutenance).

### K. Recul critique

**74.** Limites à assumer :
- **Données d'entraînement** majoritairement simulées : les performances réelles restent à
  mesurer sur des sessions enregistrées.
- **Un seul processus avec état en mémoire** (tampons, baseline, hystérésis) : pas de
  redondance ; un redémarrage refait le préchauffage.
- **Horodatage à la réception** : la latence réseau fausse légèrement les fenêtres.
- **Pickle** : chargement de code, à réserver à des fichiers de confiance.
- **API** : sans `ADMIN_TOKEN` pas de protection ; `/recording` est ouvert.
- **Capteurs bas de gamme** : MQ-2 peu sélectif (ne distingue pas bien les gaz), DHT22 lent.
- Le contrat `BACKEND_CONTRACT.md` dit « toujours les 3 types » alors qu'il y en a 4 (petit
  écart de documentation, depuis l'ajout de l'inondation).
- Pas de tests automatisés présents dans le dépôt (voir 73).

**75.** Le moteur est déjà conçu « un pipeline par appareil », mais un seul processus
deviendrait le goulot. Pistes : **partitionner les appareils** entre plusieurs instances
(abonnements partagés MQTT 5 `$share/...` ou découpage par `device_id`, en gardant un appareil
toujours sur la même instance pour son état), sortir l'état dans un stockage partagé si
besoin, broker MQTT en cluster, rétention et compression TimescaleDB, et surveillance
(métriques Prometheus/Grafana à partir de `/health`).
