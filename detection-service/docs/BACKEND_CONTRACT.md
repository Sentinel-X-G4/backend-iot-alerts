# Contrat de sortie : résultats envoyés au backend

> **Transport par défaut : MQTT.** Le payload ci-dessous est publié sur
> `sentinelx/{device_id}/detection` (QoS 1, `MQTT_RESULT_TOPIC`). La route HTTP décrite
> ici n'est utilisée que si `MQTT_RESULT_TOPIC` est vide.

> Non utilisée dans la pile : backend-api lit ces résultats en base (`detection.predictions`).

JSON Schema du corps : [`schemas/alert_payload.schema.json`](schemas/alert_payload.schema.json)

## Requête

```
POST {BACKEND_URL}{BACKEND_ALERT_ROUTE}       défaut : POST http://<backend>/api/alerts
Content-Type: application/json
Authorization: Bearer <BACKEND_TOKEN>         seulement si BACKEND_TOKEN est défini
```

Le service envoie :

- à **chaque changement d'état** d'un appareil (`reason: "state_change"`) : statut global,
  activation ou désactivation d'une alerte, source d'une alerte (modèle ou règle), ou état
  de l'appareil (`device_state`) ;
- en **heartbeat** toutes les `HEARTBEAT_INTERVAL_S` secondes (10 s) quand rien ne change
  (`reason: "heartbeat"`).

## Corps

```json
{
  "device_id": "esp01",
  "timestamp": "2026-10-05T14:20:00.500Z",
  "status": "fuite_gaz",
  "device_state": "ok",
  "reason": "state_change",
  "alerts": [
    {"type": "feu", "active": false, "confidence": 0.04, "since": null, "source": "model"},
    {"type": "fuite_gaz", "active": true, "confidence": 0.91, "since": "2026-10-05T14:19:58.000Z", "source": "model"},
    {"type": "presence", "active": true, "confidence": 0.77, "since": "2026-10-05T14:15:02.000Z", "source": "model"}
  ],
  "metrics": {
    "pir_ratio": 0.8, "cam_ratio": 1.0, "gas_mean": 640.2, "gas_max": 652.0, "gas_min": 628.0,
    "gas_slope": 3.1, "gas_delta_baseline": 410.5, "gas_do_ratio": 1.0, "temp_last": 22.6,
    "hum_last": 44.0, "temp_delta_long": 0.1, "hum_delta_long": -0.3, "temp_slope_long": 0.05,
    "n_samples_short": 10.0
  },
  "model_version": "orange-rf-v1"
}
```

| Champ | Type | Description |
|---|---|---|
| `device_id` | string | Appareil (extrait du topic MQTT). |
| `timestamp` | string ISO 8601 UTC (ms, `Z`) | Fin de la fenêtre analysée (horloge du service). |
| `status` | `feu` \| `fuite_gaz` \| `presence` \| `aucune` | Alerte active **la plus prioritaire** : `feu` > `fuite_gaz` > `presence` > `aucune`. |
| `device_state` | `ok` \| `warming_up` \| `no_data` \| `stale` | `ok` = prédiction faite. Sinon, raison de l'absence de prédiction (voir plus bas). |
| `reason` | `state_change` \| `heartbeat` | Pourquoi ce message est envoyé. |
| `alerts` | tableau, toujours les 3 types | Détail par type d'alerte (les alertes ne sont pas exclusives). |
| `alerts[].active` | bool | Alerte active après lissage et hystérésis. |
| `alerts[].confidence` | nombre 0–1 | Probabilité lissée (1.0 si forcée par une règle). |
| `alerts[].since` | string ISO ou `null` | Début de l'alerte active, `null` si inactive. |
| `alerts[].source` | `model` \| `rule` | `rule` = forcée par le filet de sécurité gaz (seuil critique MQ-2 ou sortie DO), indépendamment du modèle. |
| `metrics` | objet nombre ou `null` | Features de la fenêtre (mêmes noms que l'entraînement). `null` = indisponible (ex. DHT22 muet). |
| `model_version` | string | Version du modèle (`MODEL_VERSION`). |

### `device_state`

| Valeur | Signification | Alertes dans le payload |
|---|---|---|
| `ok` | Prédiction normale. | à jour |
| `warming_up` | Préchauffage des capteurs (`WARMUP_SECONDS`, 120 s) ou initialisation de la baseline gaz. | toutes inactives |
| `no_data` | Trop peu de mesures dans la fenêtre courte. | **dernier état connu, figé** |
| `stale` | Plus de message depuis `STALE_AFTER_S` (10 s) : appareil hors ligne ? | **dernier état connu, figé** |

Pour `no_data` et `stale`, le backend doit considérer les alertes comme **non confirmées**
(ex. afficher « capteur hors ligne, dernière alerte : … »). Il ne doit pas les annuler en
silence : un capteur qui se tait pendant un feu reste une situation dangereuse.

## Réponses attendues

| Code | Effet côté service |
|---|---|
| `2xx` (202 conseillé) | Accepté. |
| `408`, `425`, `429`, `5xx`, timeout, erreur réseau | Réessayé (`BACKEND_MAX_RETRIES` = 5, backoff exponentiel de 0,5 s à 10 s, avec jitter). |
| autre `4xx` (400, 401, 403, 404, 422…) | **Abandonné** et journalisé en erreur : c'est un problème de contrat ou de configuration. |

Répondre vite (timeout client : `BACKEND_TIMEOUT_S` = 5 s). Faire les traitements longs
(notifications push, e-mails) en tâche de fond.

## Garanties et recommandations

- **Ordre** : un seul envoi à la fois, les messages d'un appareil arrivent dans l'ordre.
  Après une reprise, se fier quand même à `timestamp` pour déterminer le dernier état.
- **Doublons possibles** : un message dont la réponse s'est perdue est renvoyé. Rendre la
  route idempotente sur `(device_id, timestamp)`.
- **Perte possible** : si le backend est injoignable longtemps, la file du service
  (`BACKEND_QUEUE_SIZE` = 500) jette d'abord les heartbeats, puis les changements d'état
  les plus anciens. Le heartbeat suivant (≤ 10 s) resynchronise l'état courant.
- **Notification** : notifier l'utilisateur sur `reason == "state_change"` quand une alerte
  `feu` ou `fuite_gaz` passe à `active: true`. Les heartbeats servent au suivi « vu à » et
  à détecter un service de détection arrêté (aucun message depuis plus de 2 × 10 s).
- **Historique** : le service garde déjà tout dans sa base (`detection.predictions`,
  `detection.sensor_readings`…). Le backend peut lire ces tables directement s'il partage
  la base, plutôt que de tout redupliquer.

## Autres points d'entrée utiles (API du service, port 8000)

| Route | Usage pour le backend |
|---|---|
| `GET /status/{device_id}` | Dernier résultat d'un appareil (même format que le POST). Utile au démarrage du backend. |
| `GET /health` | Santé du service : MQTT, base, modèle, dernière mesure par appareil. |
