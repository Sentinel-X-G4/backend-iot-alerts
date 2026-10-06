# Service de détection temps réel

Surveillance d'une pièce : un ESP8266 (PIR, MQ-2, DHT22) et une caméra publient en MQTT.
Ce service calcule des métriques sur des fenêtres glissantes et les passe à un modèle
entraîné dans **Orange Data Mining**. Il lisse les prédictions, applique des règles de
sécurité et envoie les alertes (`presence`, `fuite_gaz`, `feu`) au backend. Tout est stocké
dans PostgreSQL, y compris les sessions étiquetées qui servent à construire le jeu
d'entraînement.

## Architecture

```
            MQTT (maison/{device_id}/capteurs, /camera)
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
     │   │ (tampons  │   │ (fonction  │   │ rules/orange/ │  │                  schéma « detection »
     │   │ circulaires)  │ pure)      │   │ sklearn       │  │
     │   └───────────┘   └─────▲──────┘   └──────┬────────┘  │
     │        baseline gaz ────┘                 ▼           │
     │                                  postprocess          │      BackendSender
     │                         (lissage, hystérésis,         │──▶ (file prioritaire, ──▶ POST /api/alerts
     │                          filet de sécurité, priorité) │     httpx, backoff)
     └───────────── horloge : tick toutes les 0,5 s ─────────┘
                              │
                     API FastAPI :8000  (/health, /status, /recording, /admin)
```

| Module | Rôle |
|---|---|
| `config.py` | Toute la configuration (`pydantic-settings`, `.env`). |
| `schemas.py` | Contrats : messages MQTT entrants, payload backend, corps de l'API. |
| `mqtt_client.py` | Abonnement, validation, dispatch, reconnexion avec backoff. |
| `buffers.py` | Tampons circulaires horodatés par appareil et par signal. |
| `features.py` | **Calcul pur des features**, seule source de vérité (temps réel = entraînement). |
| `baseline.py` | Baseline lente du MQ-2 (médiane initiale puis EMA, gelée pendant les alertes). |
| `predictors/` | `RuleBasedPredictor`, `OrangePredictor` (.pkcls), `SklearnPredictor` (.joblib). |
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

## Démarrage rapide (Docker)

```bash
cp .env.example .env
docker compose up --build -d          # service + Mosquitto + TimescaleDB + faux backend
docker compose logs -f detection-service fake-backend

# Dans un autre terminal : données simulées
docker compose --profile sim run --rm simulator
```

| URL | Contenu |
|---|---|
| http://localhost:8000/health | santé (MQTT, base, modèle, appareils) |
| http://localhost:8000/status/esp01 | dernier résultat d'un appareil |
| http://localhost:8000/docs | documentation interactive de l'API |
| http://localhost:8080/api/devices | dernier état reçu par le faux backend |

Pour une démo plus rapide, réduire `WARMUP_SECONDS` (ex. 20) dans `.env`.

### Intégration avec les autres conteneurs

- **Base PostgreSQL** : elle tourne dans son propre conteneur (`postgres`, profil
  `local-db`). Pour utiliser une autre base, par exemple celle du backend, retirer
  `local-db` de `COMPOSE_PROFILES` et définir `DATABASE_URL`. Les tables sont créées par
  Alembic au démarrage, dans le schéma dédié `DB_SCHEMA` (`detection`), sans toucher aux
  tables du backend. Si la base est injoignable, le service continue de détecter : les
  écritures restent en mémoire (bornée) et sont réessayées.
- **Réseau** : la pile crée le réseau Docker `iot-net`. Le backend et le conteneur
  producteur MQTT le rejoignent avec `networks: { iot: { external: true, name: iot-net } }`.
- **TimescaleDB** : `TIMESCALEDB=true` crée des hypertables pour `sensor_readings`,
  `camera_events` et `feature_windows`. Mettre `false` sur un PostgreSQL standard.

## Développement local (sans Docker pour le service)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # + ".[orange]" pour charger les .pkcls
docker compose up -d mosquitto postgres fake-backend
export MQTT_HOST=localhost DATABASE_URL=postgresql+asyncpg://iot:iot@localhost:5432/iot \
       BACKEND_URL=http://localhost:8080
detection-service
python tools/simulator.py --sequence normal:150,fuite_gaz:45,normal:60
```

Les variables d'environnement ont priorité sur le fichier `.env`.

### Tests

```bash
pytest                                   # tests unitaires et d'intégration (simulateur)
TEST_DATABASE_URL=postgresql+asyncpg://iot:iot@localhost:5432/iot pytest tests/test_postgres.py
```

`tests/test_orange.py` entraîne de vrais modèles Orange (ignoré si `orange3` est absent).
Le test du vrai broker MQTT est ignoré si aucun Mosquitto n'écoute sur `localhost:1883`.

## Configuration

Toutes les variables sont listées et commentées dans [`.env.example`](.env.example). Les
principales :

| Variable | Défaut | Rôle |
|---|---|---|
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS` | `localhost`, `1883` | Broker. |
| `MQTT_SENSOR_TOPIC`, `MQTT_CAMERA_TOPIC` | `maison/{device_id}/capteurs`, `…/camera` | Motifs de topics. |
| `INFERENCE_INTERVAL_S` | `0.5` | Période d'inférence (horloge). |
| `SHORT_WINDOW_S`, `LONG_WINDOW_S` | `2`, `60` | Fenêtres courte et longue. |
| `WARMUP_SECONDS` | `120` | Préchauffage sans prédiction. |
| `BASELINE_TAU_S` | `600` | Constante de temps de la baseline gaz. |
| `PREDICTOR` | `rules` | `rules`, `orange` ou `sklearn`. |
| `MODEL_MODE` | `multilabel` | `multilabel` (un modèle binaire par alerte) ou `multiclass`. |
| `MODEL_PATH` / `MODEL_PATHS` | — | Modèle unique (multi-classe) / JSON type → chemin (multi-label). |
| `ALERT_THRESHOLD_ON/OFF`, `ALERT_K_ON`, `ALERT_M_OFF` | `0.6`, `0.4`, `3`, `6` | Hystérésis. `ALERT_OVERRIDES` pour régler un type. |
| `SAFETY_GAS_CRITICAL`, `SAFETY_GAS_DO_TICKS` | `800`, `4` | Filet de sécurité gaz. |
| `BACKEND_URL`, `BACKEND_ALERT_ROUTE`, `BACKEND_TOKEN` | —, `/api/alerts` | Envoi des alertes. |
| `HEARTBEAT_INTERVAL_S` | `10` | Heartbeat vers le backend. |
| `DATABASE_URL`, `DB_SCHEMA`, `TIMESCALEDB` | —, `detection`, `false` | Stockage. |

### Choix par défaut retenus

Ces choix ont été validés avant le développement ou pris par défaut. Ils sont tous
configurables :

- **Base** : PostgreSQL/TimescaleDB dans un conteneur séparé, joint par `DATABASE_URL`.
  Les tables sont dans un schéma dédié pour pouvoir partager plus tard la base du backend.
- **Backend** : pas encore développé. Le contrat est dans `docs/BACKEND_CONTRACT.md` et un
  faux backend de référence est fourni. Route par défaut `POST /api/alerts`, jeton Bearer
  optionnel.
- **MQTT** : le format d'entrée est imposé par ce service (`docs/MQTT_CONTRACT.md`). Le
  conteneur qui relaie l'ESP et la caméra s'y conforme.
- **Modèle** : mode **multi-label** par défaut. Tant qu'aucun modèle n'est entraîné, le
  `RuleBasedPredictor` est utilisé.
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
4. Statut global par priorité : `feu` > `fuite_gaz` > `presence` > `aucune`.

## Workflow d'entraînement avec Orange

1. **Collecter des sessions étiquetées.** Pendant que le service tourne, chaque situation
   réelle est enregistrée comme une session :

   ```bash
   curl -X POST localhost:8000/recording/start -H 'Content-Type: application/json' \
        -d '{"label": "fuite_gaz", "device_id": "esp01", "notes": "briquet sans flamme à 20 cm"}'
   # … reproduire la situation pendant 1 à 3 minutes …
   curl -X POST localhost:8000/recording/stop -H 'Content-Type: application/json' -d '{"device_id": "esp01"}'
   ```

   Labels : `aucune`, `presence`, `fuite_gaz`, `feu`. Pour des alertes simultanées, les
   combiner avec `+` (ex. `presence+fuite_gaz`). Viser **plusieurs sessions par classe**
   (≥ 5), dans des conditions variées : jours, températures, distances. Enregistrer aussi
   beaucoup de `aucune`, y compris des situations pièges : cuisine, déodorant, porte ouverte.

   Le simulateur peut enregistrer ses scénarios pour tester la chaîne :
   `python tools/simulator.py --sequence normal:60,fuite_gaz:60,feu:90 --record`.

2. **Exporter en CSV.**

   ```bash
   python tools/export_dataset.py --list-sessions
   # Multi-label : un fichier par alerte, cible binaire <type>/aucune
   python tools/export_dataset.py -o exports/feu.csv --target feu --orange-flags
   python tools/export_dataset.py -o exports/fuite_gaz.csv --target fuite_gaz --orange-flags
   python tools/export_dataset.py -o exports/presence.csv --target presence --orange-flags
   # Multi-classe : labels bruts
   python tools/export_dataset.py -o exports/dataset.csv --orange-flags
   ```

   Colonnes : les 14 features, puis `label` et `session_id`. `--orange-flags` écrit
   `cD#label` et `mS#session_id` : Orange prend directement `label` comme cible et
   `session_id` comme méta.

3. **Entraîner dans Orange.** `File` → `Data Sampler` ou `Test and Score`, avec un découpage
   **par session** : réserver des `session_id` entiers pour le test, par exemple en filtrant
   les sessions avec `Select Rows`. Ne jamais mélanger les fenêtres au hasard : deux fenêtres
   consécutives (0,5 s d'écart) sont presque identiques, et un mélange aléatoire donne des
   scores excellents mais faux. Choisir un learner (Random Forest, régression logistique…)
   puis `Save Model` → fichier `.pkcls`.

4. **Déployer.**

   ```bash
   cp feu.pkcls fuite_gaz.pkcls presence.pkcls models/
   # .env
   PREDICTOR=orange            # nécessite INSTALL_ORANGE=true pour l'image Docker
   MODEL_MODE=multilabel
   MODEL_PATHS={"feu": "/models/feu.pkcls", "fuite_gaz": "/models/fuite_gaz.pkcls", "presence": "/models/presence.pkcls"}
   MODEL_VERSION=orange-rf-v1
   ```

   Au démarrage, le service vérifie que chaque colonne attendue par le modèle existe dans ses
   features et identifie la classe positive. En cas de problème, il s'arrête avec un message
   explicite. Les types d'alerte sans modèle valent 0, mais le filet de sécurité gaz reste
   actif.

   **Plan B sans orange3 dans l'image** : convertir sur la machine qui a Orange :

   ```bash
   python tools/export_orange_to_joblib.py models/feu.pkcls   # → feu.joblib + feu.json
   # PREDICTOR=sklearn, MODEL_PATHS={"feu": "/models/feu.joblib", ...}
   ```

   La conversion reproduit l'imputation d'Orange (testé : probabilités identiques). Elle
   refuse les modèles avec d'autres prétraitements (normalisation…). Dans ce cas, utiliser
   `PREDICTOR=orange`.

5. **Recharger sans redémarrer** : `docker compose kill -s HUP detection-service`, ou
   `curl -X POST localhost:8000/admin/reload-model -H "Authorization: Bearer $ADMIN_TOKEN"`.
   Si le nouveau modèle est invalide, l'ancien est conservé et l'erreur apparaît dans
   `/health`.

## Simulateur

```bash
python tools/simulator.py --sequence normal:150,presence:30,fuite_gaz:45,normal:60,feu:120,capteur_muet:20
```

| Scénario | Comportement |
|---|---|
| `normal` | air propre, légère dérive du MQ-2, pas de mouvement |
| `presence` | PIR (avec temporisation de 5 s) et caméra à 1 par intermittence |
| `fuite_gaz` | `gas_raw` monte d'environ 450, température stable |
| `feu` | fumée (`gas_raw` +350) **et** température +3 °C/min |
| `capteur_muet` | plus aucun message |
| `dht_nan` | lectures DHT22 ratées (`null`) |

Options : `--device`, `--rate`, `--camera-rate`, `--loop`, `--device-warmup`, `--record`.

## Base de données

Tables du schéma `detection` : `sensor_readings`, `camera_events`, `feature_windows`
(une colonne par feature, plus `session_id` et `label`), `predictions` (alertes en JSONB,
écrites à chaque envoi au backend), `recording_sessions`. Migrations dans `migrations/`
(`alembic upgrade head`, lancé automatiquement si `RUN_MIGRATIONS=true`). Toute
modification de `storage/tables.py` doit s'accompagner d'une migration.

Volume indicatif par appareil : environ 430 000 mesures brutes et 170 000 fenêtres par
jour. Avec TimescaleDB, prévoir une politique de rétention, par exemple
`SELECT add_retention_policy('detection.sensor_readings', INTERVAL '30 days');`.
Garder les fenêtres étiquetées.
