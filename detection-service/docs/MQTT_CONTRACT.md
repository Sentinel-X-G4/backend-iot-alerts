# Contrat d'entrée MQTT

Ce document décrit ce que le service de détection **attend** sur MQTT. Le conteneur qui
relaie l'ESP8266 et la caméra doit publier exactement ce format.

JSON Schema : [`schemas/sensor_message.schema.json`](schemas/sensor_message.schema.json),
[`schemas/camera_message.schema.json`](schemas/camera_message.schema.json).

## Connexion

| Paramètre | Pile complète (dépôt `main`) | Lancement local (broker de test) | Variable du service |
|---|---|---|---|
| Broker | `mqtt.sentinel.lan:8883` (réseau `sentinel-back`) | `localhost:1883` | `MQTT_HOST`, `MQTT_PORT` |
| TLS | obligatoire (CA `ca.crt`) | désactivé | `MQTT_TLS`, `MQTT_TLS_CA_CERTS` |
| Authentification | compte `detection` (+ ACL) | aucune | `MQTT_USERNAME`, `MQTT_PASSWORD` |
| QoS | 0 en entrée, 1 pour les résultats | idem | `MQTT_QOS`, `MQTT_RESULT_QOS` |

Comptes et droits de la pile complète : `infrastructure/mosquitto/config/acl`
(`sentinel_iot` = ESP, `vision` = caméra, `detection` = ce service, `iot-backend` = backend-api).

## Topics

| Topic | Fréquence | Contenu |
|---|---|---|
| `sentinelx/{device_id}/telemetry` | ~5 msg/s (toutes les 200 ms) | mesures de l'ESP8266 |
| `sentinelx/{device_id}/camera` | libre (1 msg/s conseillé) | détection de personne |
| `sentinelx/{device_id}/alert` | ponctuel | alerte brute de l'ESP → ligne dans `public.alerts` |
| `sentinelx/{device_id}/detection` | changement d'état + heartbeat 10 s | **sortie** : résultats du service (QoS 1) |

- `{device_id}` : identifiant de l'appareil (sans `/`), ex. `esp01`. La caméra d'une pièce
  doit publier avec le **même `device_id`** que l'ESP de cette pièce : les deux flux sont
  combinés par appareil.
- Les motifs sont configurables (`MQTT_SENSOR_TOPIC`, `MQTT_CAMERA_TOPIC`) et doivent
  contenir exactement un `{device_id}`.
- Charge utile : **JSON UTF-8**, un objet par message. Les champs inconnus sont ignorés.

## `sentinelx/{device_id}/telemetry`

```json
{"ts": 1728136800123, "temp": 22.4, "hum": 45.1, "pir": 1, "gas_raw": 312, "gas_do": 0, "warmup": false}
```

| Champ | Type | Obligatoire | Règles |
|---|---|---|---|
| `ts` | entier (ms epoch) | non | Informatif seulement. Le service horodate à la **réception**. |
| `temp` | nombre ou `null` | non | °C, −40 → 80. `null` si le DHT22 n'a pas été lu (au plus une lecture toutes les 2 s) ou si la lecture a échoué. `NaN` et les valeurs hors plage sont traités comme `null`. |
| `hum` | nombre ou `null` | non | %HR, 0 → 100. Mêmes règles que `temp`. |
| `pir` | `0`/`1` (ou booléen) | **oui** | Sortie du HW-416, sans transformation. |
| `gas_raw` (ou `gas`) | entier | **oui** | Sortie AO du MQ-2 : valeur ADC brute **0 → 1023**, sans conversion. |
| `gas_do` | `0`/`1` | non | Sortie DO **brute** du MQ-2 : `0` = seuil dépassé (actif bas), `1` = normal. Omettre le champ si DO n'est pas câblé. |
| `warmup` | booléen | non (défaut `false`) | `true` tant que l'appareil sait que ses capteurs chauffent (ex. 60 premières secondes après boot). |

**Message rejeté** (journalisé, ignoré) : JSON invalide, `pir` ou `gas_raw` absents,
`gas_raw` hors 0–1023, `gas_do` différent de 0/1.

**Bonnes pratiques côté producteur**

- Publier toutes les mesures, même quand rien ne change : le service calcule des proportions
  et des pentes sur des fenêtres de 2 s et 60 s. Il faut au moins 5 messages par fenêtre de
  2 s (`MIN_SAMPLES_SHORT`), sinon le statut passe à `no_data`.
- Ne pas filtrer ni lisser les valeurs : le service s'en charge, et le modèle est entraîné
  sur des valeurs brutes.
- Ne pas répéter la dernière température quand le DHT22 n'est pas lu : envoyer `null`.
- Plus de message pendant `STALE_AFTER_S` (10 s) : l'appareil passe en statut `stale`.

## `sentinelx/{device_id}/camera`

```json
{"ts": 1728136800150, "person": true}
```

| Champ | Type | Obligatoire | Règles |
|---|---|---|---|
| `ts` | entier (ms epoch) | non | Informatif. |
| `person` | booléen | **oui** | Personne détectée dans l'image. |

La feature `cam_ratio` est la proportion de `true` reçus sur les 2 dernières secondes. Si
la caméra n'a rien publié dans la fenêtre, sa dernière valeur reste valable pendant
`CAMERA_HOLD_S` (5 s). Si la caméra ne publie **qu'aux changements**, augmenter
`CAMERA_HOLD_S` en conséquence. Le mieux reste de publier périodiquement (≥ 1 msg/s).

## `sentinelx/{device_id}/alert`

```json
{"type": "pir", "value": true}
```

`type` : chaîne de 1 à 64 caractères (obligatoire). `value` : facultatif ; `false` = fin de
l'alerte, ignorée. Chaque message accepté crée une alerte `medium` dans `public.alerts`
(source `esp/{device_id}`), diffusée au dashboard par backend-api. Motif : `MQTT_ESP_ALERT_TOPIC`.

## `sentinelx/{device_id}/detection` (sortie)

Publié par le service, à titre informatif : backend-api ne lit plus MQTT, il lit le même
résultat dans `detection.predictions`. Le corps est exactement le payload décrit dans
[`BACKEND_CONTRACT.md`](BACKEND_CONTRACT.md) (statut, alertes, métriques). Avec
`MQTT_RESULT_TOPIC` vide, ce payload part en HTTP vers `BACKEND_URL` à la place.

## Tester son producteur

```bash
# Écouter ce que reçoit le broker
mosquitto_sub -h localhost -t 'sentinelx/#' -v

# Publier un message à la main
mosquitto_pub -h localhost -t sentinelx/esp01/telemetry \
  -m '{"temp": null, "hum": null, "pir": 0, "gas_raw": 180, "gas_do": 1}'

# Vérifier que le service l'accepte (compteurs received / invalid)
curl -s localhost:8000/health | jq .mqtt
```
