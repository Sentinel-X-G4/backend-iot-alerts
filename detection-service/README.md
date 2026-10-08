# Service de détection temps réel

Surveillance d'une pièce : un ESP8266 (PIR, MQ-2, DHT22) et une caméra publient en MQTT.
Ce service calcule des métriques sur des fenêtres glissantes et les passe à un modèle
entraîné dans **Orange Data Mining**. Il lisse les prédictions, applique des règles de
sécurité et envoie les alertes (`presence`, `fuite_gaz`, `feu`, `inondation`) au backend et à
l'alarme de l'ESP. Tout est stocké
dans PostgreSQL, y compris les sessions étiquetées exportées vers Orange.

## Architecture

```
            MQTT (sentinelx/{device_id}/telemetry, /camera)
                              │
                ┌─────────────▼─────────────┐
                │ mqtt_client  validation   │── message invalide → log + ignoré
                │ (Pydantic, reconnexion)   │
                └─────────────┬─────────────┘
                              │ SensorReading / CameraEvent (horodatés à la réception)
     ┌────────────────────────┼──────────────────────────────┐
     │                        ▼                              │
     │   engine : un DevicePipeline par appareil             │      BatchWriter
     │   ┌───────────┐   ┌────────────┐   ┌───────────────┐  │  (file bornée, lots,
     │   │ buffers   │──▶│ features   │──▶│ predictor     │  │──▶ reprises) ──▶ PostgreSQL
     │   │ (tampons  │   │ (fonction  │   │ modèles       │  │                  schéma « detection »
     │   │ circulaires)  │ pure)      │   │ Orange .pkcls │  │
     │   └───────────┘   └─────▲──────┘   └──────┬────────┘  │
     │        baseline gaz ────┘                 ▼           │
     │                                  postprocess          │      BackendSender
     │                         (lissage, hystérésis,         │──▶ (file prioritaire, ──▶ POST /api/alerts
     │                          filet de sécurité, priorité) │     httpx, backoff)
     └───────────── horloge : tick toutes les 0,5 s ─────────┘
                              │
                     API FastAPI :8000  (/health, /status, /recording, /admin, /devices)
```

| Module | Rôle |
|---|---|
| `config.py` | Toute la configuration (`pydantic-settings`, `.env`). |
| `schemas.py` | Contrats : messages MQTT entrants, payload backend, corps de l'API. |
| `mqtt_client.py` | Abonnement, validation, dispatch, reconnexion avec backoff. |
| `buffers.py` | Tampons circulaires horodatés par appareil et par signal. |
| `features.py` | **Calcul pur des features**, seule source de vérité (temps réel = entraînement). |
| `baseline.py` | Baseline lente du MQ-2 (médiane initiale puis EMA, gelée pendant les alertes). |
| `predictors/` | `OrangePredictor` (.pkcls de `models/`, détectés automatiquement). |
| `commands.py` | Commandes vers l'ESP, dont l'alarme déclenchée par les alertes (`EspAlarm`). |
| `postprocess.py` | Lissage, hystérésis K/M, filet de sécurité gaz, priorité des statuts. |
| `engine.py` | Orchestration par appareil, états (`warming_up`, `no_data`, `stale`), payloads. |
| `backend_client.py` | Envoi HTTP non bloquant avec file bornée et retries. |
| `storage/` | Interface `Storage`, implémentation SQL (SQLAlchemy async), écrivain par lots. |
| `api.py` / `service.py` | API HTTP, cycle de vie, arrêt propre (SIGINT/SIGTERM), rechargement (SIGHUP). |
| `simulation.py` | Générateur de scénarios (simulateur et tests d'intégration). |

### Contrats d'interface

- **Entrée MQTT** (pour le conteneur producteur) : [`docs/MQTT_CONTRACT.md`](docs/MQTT_CONTRACT.md)
- **Sortie backend** (route à implémenter) : [`docs/BACKEND_CONTRACT.md`](docs/BACKEND_CONTRACT.md)
- JSON Schema générés depuis le code : [`docs/schemas/`](docs/schemas/) (`python tools/export_schemas.py`)

## Démarrage (Docker)

Le service n'a pas de compose propre : il est lancé par le **seul** `docker-compose.yml` du
dépôt [`main`](https://github.com/Sentinel-X-G4/main) (conteneur `sentinel-detection`), avec
Mosquitto en MQTTS et la base `database`.

```bash
# depuis main/
make up
make sim                     # données simulées
```

| URL (port `DETECTION_API_PORT` du `.env` de main) | Contenu |
|---|---|
| http://localhost:8000/health | santé (MQTT, base, modèle, appareils) |
| http://localhost:8000/status/esp01 | dernier résultat d'un appareil |
| http://localhost:8000/docs | documentation interactive de l'API |
| `POST /devices/{id}/alert\|buzzer\|led\|screen\|reset` | commandes vers l'ESP (jeton `ADMIN_TOKEN`), relayées depuis le dashboard par backend-api ; voir [`docs/MQTT_CONTRACT.md`](docs/MQTT_CONTRACT.md) |

Pour une démo plus rapide, réduire `WARMUP_SECONDS` (ex. 20) dans le `.env` de main.

### Intégration avec les autres conteneurs

- **Base PostgreSQL** : c'est la base unique du projet, dépôt `database`. Son schéma, y
  compris le schéma `detection` de ce service, est défini à un seul endroit :
  `database/db/init/`. Le service ne crée aucune table. Sans `DATABASE_URL`, rien n'est
  persisté. Si la base est injoignable, le service continue de détecter : les écritures
  restent en mémoire (bornée) et sont réessayées.
- **Alertes et états** : le service est le seul abonné MQTT ; backend-api lit ses résultats
  en base (`alerts`, `detection.predictions`).

## Développement local (sans Docker pour le service)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
# un broker MQTT local, par exemple : docker run -d -p 1883:1883 eclipse-mosquitto:2 \
#   mosquitto -c /mosquitto-no-auth.conf
export MQTT_HOST=localhost MODELS_DIR=models
detection-service
python tools/simulator.py --sequence normal:150,fuite_gaz:45,normal:60
```

Les variables d'environnement ont priorité sur le fichier `.env`.

### Tests

`pytest` est configuré dans `pyproject.toml` (`testpaths = ["tests"]`, extra `[dev]`), mais le
dossier `tests/` n'est pas présent dans le dépôt : il n'y a pas de suite de tests exécutable.
Validation actuelle : `make sim` depuis main/ et `GET /health`.

## Configuration

Toutes les variables sont listées et commentées dans [`.env.example`](.env.example). Les
principales :

| Variable | Défaut | Rôle |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS` | `localhost`, `1883` | Broker. |
| `MQTT_SENSOR_TOPIC`, `MQTT_CAMERA_TOPIC` | `sentinelx/{device_id}/telemetry`, `…/camera` | Motifs de topics. |
| `INFERENCE_INTERVAL_S` | `0.5` | Période d'inférence (horloge). |
| `SHORT_WINDOW_S`, `LONG_WINDOW_S` | `2`, `60` | Fenêtres courte et longue. |
| `WARMUP_SECONDS` | `120` | Préchauffage sans prédiction. |
| `BASELINE_TAU_S` | `600` | Constante de temps de la baseline gaz. |
| `MODELS_DIR` | `/models` | Dossier des `.pkcls` exportés par Orange. |
| `MODEL_MODE` | `multilabel` | `multilabel` (un modèle binaire par alerte) ou `multiclass`. |
| `MODEL_PATH` / `MODEL_PATHS` | — | Chemins explicites, prioritaires sur `MODELS_DIR`. |
| `ALERT_THRESHOLD_ON/OFF`, `ALERT_K_ON`, `ALERT_M_OFF` | `0.6`, `0.4`, `3`, `6` | Hystérésis. `ALERT_OVERRIDES` pour régler un type. |
| `SAFETY_GAS_CRITICAL`, `SAFETY_GAS_DO_TICKS` | `800`, `4` | Filet de sécurité gaz. |
| `ESP_ALARM_TYPES`, `ESP_ALARM_OFF_DELAY_S` | les 4 types, `5` | Alertes qui déclenchent l'alarme de l'ESP. |
| `BACKEND_URL`, `BACKEND_ALERT_ROUTE`, `BACKEND_TOKEN` | —, `/api/alerts` | Envoi des alertes. |
| `HEARTBEAT_INTERVAL_S` | `10` | Heartbeat vers le backend. |
| `DATABASE_URL`, `DB_SCHEMA` | —, `detection` | Stockage (schéma créé par l'infra). |

### Choix par défaut retenus

Ces choix ont été validés avant le développement ou pris par défaut. Ils sont tous
configurables :

- **Base** : PostgreSQL/TimescaleDB (dépôt `database`), jointe par `DATABASE_URL`. Les
  tables du service sont dans un schéma dédié (`detection`), à côté des tables communes.
- **Backend** : backend-api lit la base. L'envoi HTTP (`docs/BACKEND_CONTRACT.md`, route
  `POST /api/alerts`) n'est utilisé que si `MQTT_RESULT_TOPIC` est vide.
- **MQTT** : le format d'entrée est imposé par ce service (`docs/MQTT_CONTRACT.md`). Le
  conteneur qui relaie l'ESP et la caméra s'y conforme.
- **Modèle** : mode **multi-label** par défaut. Au moins un `.pkcls` est obligatoire dans
  `models/` : sans modèle, le service refuse de démarrer.
- **Caméra** : publication périodique supposée. Sinon, augmenter `CAMERA_HOLD_S`.
- **Appareil `no_data` / `stale`** : les alertes ne sont pas recalculées. Le dernier état est
  renvoyé figé, avec `device_state` pour que le backend sache qu'il n'est plus confirmé.
- **Payload** : en plus du format demandé, il contient `device_state`, `reason`
  (`state_change` / `heartbeat`) et **toutes** les features dans `metrics`.

## Fonctionnement détaillé

### Features (fenêtre courte 2 s, fenêtre longue 60 s)

| Feature | Calcul |
|---|---|
| `pir_ratio`, `cam_ratio` | proportion de 1 / `true` sur la fenêtre courte |
| `gas_mean`, `gas_max`, `gas_min` | sur la fenêtre courte |
| `gas_slope` | pente par moindres carrés (unités ADC/s) |
| `gas_delta_baseline` | `gas_mean − baseline` |
| `gas_do_ratio` | proportion d'alerte DO (0 si DO absent) |
| `temp_last`, `hum_last` | dernière lecture valide (manquante si plus vieille que `DHT_MAX_AGE_S`) |
| `temp_delta_long`, `hum_delta_long` | dernière − première valeur sur la fenêtre longue |
| `temp_slope_long` | pente en °C/min sur la fenêtre longue |
| `n_samples_short` | nombre de mesures dans la fenêtre courte |

Une valeur manquante vaut `NaN` pour le modèle (Orange l'impute) et `null` en JSON et en base.

### États d'un appareil

`warming_up` (préchauffage, ou initialisation de la baseline sur `BASELINE_INIT_TICKS`
ticks) → `ok` (prédiction). Ensuite `no_data` si la fenêtre courte contient moins de
`MIN_SAMPLES_SHORT` mesures, et `stale` après `STALE_AFTER_S` sans message.

### Post-traitement

1. Lissage exponentiel de chaque probabilité (`SMOOTHING_ALPHA`).
2. Hystérésis par type d'alerte : activation après `K` probabilités lissées consécutives
   ≥ `threshold_on`, désactivation après `M` consécutives < `threshold_off`.
3. Filet de sécurité : `gas_max ≥ SAFETY_GAS_CRITICAL`, ou `gas_do_ratio = 1` pendant
   `SAFETY_GAS_DO_TICKS` ticks, force `fuite_gaz` (`source: "rule"`). Il reste actif même si
   le modèle plante.
4. Statut global par priorité : `feu` > `fuite_gaz` > `inondation` > `presence` > `aucune`.
5. Alarme de l'ESP : dès qu'une alerte de `ESP_ALARM_TYPES` (par défaut les quatre) est active, le
   service envoie `alert on` puis le texte de l'alerte à l'écran (`INCENDIE DETECTE`…) sur
   `sentinelx/{device_id}/cmd`. Il envoie `alert off` et `screen auto` après
   `ESP_ALARM_OFF_DELAY_S` sans alerte. Sans acquittement de l'ESP, il réessaie toutes les
   `ESP_ALARM_RETRY_S`. Le dashboard peut toujours forcer ou couper l'alarme.

La présence a par défaut `k_on = 8` (≈ 4 s), au lieu de 3 : en pièce vide, le PIR produit des
impulsions isolées de 0,6 à 0,9 s qui ne doivent pas déclencher l'alarme.

## Modèles Orange

L'entraînement se fait entièrement dans l'application **Orange Data Mining**. Le service ne
fait que charger les modèles exportés et les appliquer aux features en temps réel.

1. **Enregistrer des situations réelles.** Pendant que le service tourne, chaque situation
   provoquée devant les capteurs est enregistrée comme une session étiquetée :

   ```bash
   curl -X POST localhost:8000/recording/start -H 'Content-Type: application/json' \
        -d '{"label": "fuite_gaz", "device_id": "esp01", "notes": "briquet sans flamme à 20 cm"}'
   # … reproduire la situation pendant 1 à 3 minutes …
   curl -X POST localhost:8000/recording/stop -H 'Content-Type: application/json' -d '{"device_id": "esp01"}'
   ```

   Labels : `aucune`, `presence`, `fuite_gaz`, `feu`, `inondation`, combinables avec `+` (ex.
   `presence+fuite_gaz`). Enregistrer aussi des `aucune`, y compris des situations pièges :
   cuisine, déodorant, porte ouverte.

2. **Exporter les fenêtres en CSV pour Orange** (depuis main/) :

   ```bash
   docker compose exec -T detection-service python tools/export_dataset.py --list-sessions
   # Multi-label : un fichier par alerte, cible binaire <type>/aucune
   docker compose exec -T detection-service python tools/export_dataset.py --target feu --orange-flags > feu.csv
   # Multi-classe : labels bruts
   docker compose exec -T detection-service python tools/export_dataset.py --orange-flags > dataset.csv
   ```

   Colonnes : les 14 features, puis `label` (cible) et `session_id` (méta). Avec
   `--orange-flags`, Orange les reconnaît seul. Dans Orange, les features doivent rester
   numériques, et le découpage entraînement/test doit se faire par `session_id`.

3. **Déposer les modèles.** Exporter chaque modèle avec le widget `Save Model` dans
   `detection-service/models/` (monté sur `/models` dans le conteneur), sous ces noms :

   | Mode (`MODEL_MODE`) | Fichiers |
   |---|---|
   | `multilabel` (défaut) | `feu.pkcls`, `fuite_gaz.pkcls`, `inondation.pkcls`, `presence.pkcls` (un type sans fichier vaut 0) |
   | `multiclass` | `model.pkcls` |

   Le service s'arrête au démarrage s'il ne trouve aucun modèle. Prise en compte : au
   démarrage, ou sans redémarrer avec
   `docker compose kill -s HUP detection-service` (depuis main/) ou
   `curl -X POST localhost:8000/admin/reload-model -H "Authorization: Bearer $ADMIN_TOKEN"`.
   `/health` → `model` indique le predictor actif, les fichiers chargés et `version`
   (`orange-<empreinte>`, qui change à chaque nouveau modèle).

   Au chargement, le service vérifie que chaque colonne attendue par le modèle existe dans ses
   features et identifie la classe positive. En cas de problème, il s'arrête au démarrage
   (ou garde l'ancien modèle lors d'un rechargement) avec un message explicite. Le filet de
   sécurité gaz reste actif dans tous les cas.

   **Versions** : un `.pkcls` est un pickle Python. L'image Docker installe donc les mêmes
   `orange3` et `scikit-learn` que l'application Orange (3.40.0 / 1.5.2, arguments
   `ORANGE_VERSION` et `SKLEARN_VERSION` du `Dockerfile`). Les mettre à jour si l'application
   Orange change de version. Les modèles de départ (`tools/train_models.py`) sont versionnés ;
   un modèle déposé à la place remplace le fichier du même nom.

### Modèles de départ (données simulées)

`models/` contient quatre modèles générés par `tools/train_models.py`, en attendant assez de
sessions réelles. Le script simule des sessions étiquetées (gaz ≈ 100, 17–27 °C, 30–78 %RH,
avec des pièges dans les sessions `aucune`). Il calcule leurs fenêtres avec le vrai pipeline du
service, y ajoute les fenêtres réelles `aucune` de `exports/temoins_aucune/`, entraîne une forêt
aléatoire Orange par alerte, la valide par session, puis écrit les `.pkcls` et les CSV
d'entraînement (`exports/synthetique/`, ouvrables dans Orange). Chaque modèle ne voit que les
signaux liés à son alerte :

| Modèle | Colonnes |
|---|---|
| `presence` | `pir_ratio`, `cam_ratio` |
| `fuite_gaz` | gaz (`gas_*`), `temp_delta_long`, `temp_slope_long` |
| `feu` | gaz, `temp_last`, `temp_delta_long`, `temp_slope_long`, `hum_delta_long` |
| `inondation` | `hum_last`, `hum_delta_long` |

Pour les régénérer (mêmes versions d'Orange que le service, donc dans son image), depuis
`detection-service/` :

```bash
docker run --rm -v "$PWD":/work -w /work -e PYTHONPATH=/work/src \
    --entrypoint python sentinel-x/detection-service:dev tools/train_models.py
```

Les remplacer par des modèles entraînés sur des sessions réelles dès qu'il y en a assez.

## Simulateur

```bash
python tools/simulator.py --sequence normal:150,presence:30,fuite_gaz:45,normal:60,feu:120,normal:180,inondation:90,capteur_muet:20
```

| Scénario | Comportement |
|---|---|
| `normal` | air propre, légère dérive du MQ-2, pas de mouvement |
| `presence` | PIR (avec temporisation de 5 s) et caméra à 1 par intermittence |
| `a+b` | situations simultanées, ex. `presence+fuite_gaz` |
| `fuite_gaz` | `gas_raw` monte d'environ 450, température stable |
| `feu` | fumée (`gas_raw` +350) **et** température +3 °C/min |
| `inondation` | humidité qui monte vers 95 %RH en environ une minute |
| `capteur_muet` | plus aucun message |
| `dht_nan` | lectures DHT22 ratées (`null`) |

Options : `--device`, `--rate`, `--camera-rate`, `--loop`, `--device-warmup`.

## Base de données

Tables du schéma `detection` : `sensor_readings`, `camera_events`, `feature_windows`
(une colonne par feature, plus `session_id` et `label`), `predictions` (alertes en JSONB,
écrites à chaque envoi au backend : c'est l'état de chaque appareil lu par backend-api),
`camera_state` (dernier état de chaque caméra, une ligne par appareil, lu par backend-api),
`recording_sessions`. Elles sont créées par
`database/db/init/02_detection.sql` (hypertables TimescaleDB pour
`sensor_readings`, `camera_events` et `feature_windows`). Toute modification de
`storage/tables.py` doit y être reportée.

Le service écrit aussi la table commune `public.alerts` (`database/db/init/01_schema.sql`, module `alerts.py`) :
une ligne à l'**activation** de `feu`, `fuite_gaz`, `inondation` ou `presence` (pas à chaque tick), et une
par message `sentinelx/{device_id}/alert` de l'ESP. Ces lignes sont écrites sans attendre le
lot suivant et jamais sacrifiées quand la file est pleine. backend-api les lit en interrogeant la base
(le dashboard redemande `/api/v1/overview` toutes les 0,5 s) ; il n'y a pas de WebSocket.

Volume indicatif par appareil : environ 430 000 mesures brutes et 170 000 fenêtres par
jour. Avec TimescaleDB, prévoir une politique de rétention, par exemple
`SELECT add_retention_policy('detection.sensor_readings', INTERVAL '30 days');`.
Garder les fenêtres étiquetées.
