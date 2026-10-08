#!/usr/bin/env python
"""Génère des données étiquetées et entraîne les 4 modèles Orange (mode multi-label).

En attendant assez de sessions réelles enregistrées (POST /recording/start), ce script
fabrique des sessions simulées réalistes, en calcule les fenêtres avec le **vrai pipeline**
du service (tampons, baseline, `compute_features`), y ajoute les fenêtres réelles « aucune »
exportées, puis entraîne un classifieur binaire Orange par alerte :

    models/feu.pkcls  models/fuite_gaz.pkcls  models/inondation.pkcls  models/presence.pkcls

Valeurs normales simulées : gaz ≈ 100 (ADC), 17–27 °C, 30–78 %RH. Les sessions « aucune »
contiennent des pièges (bouffée de gaz, chauffage, humidité qui monte, PIR parasite).

Les .pkcls étant des pickles, ce script doit tourner avec les mêmes orange3 / scikit-learn que
le service, par exemple dans son image (depuis detection-service/) :

    docker run --rm -v "$PWD":/work -w /work -e PYTHONPATH=/work/src \\
        --entrypoint python sentinel-x/detection-service:dev tools/train_models.py

Les jeux de données sont aussi écrits en CSV (en-têtes Orange) dans exports/synthetique/,
pour les ouvrir dans l'application Orange.
"""

from __future__ import annotations

import argparse
import csv
import math
import pickle
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from detection_service.config import ALERT_TYPES, Settings  # noqa: E402
from detection_service.dataset import NEGATIVE, label_parts  # noqa: E402
from detection_service.engine import DevicePipeline  # noqa: E402
from detection_service.features import FEATURE_NAMES  # noqa: E402
from detection_service.predictors import RuleBasedPredictor  # noqa: E402
from detection_service.schemas import CameraEvent, SensorMessage, SensorReading  # noqa: E402
from detection_service.simulation import RoomSimulator, scenario_parts  # noqa: E402

# Colonnes vues par chaque modèle : uniquement les signaux physiquement liés à l'alerte,
# pour éviter qu'un modèle apprenne une corrélation fortuite des données simulées.
MODEL_FEATURES: dict[str, tuple[str, ...]] = {
    "feu": ("gas_mean", "gas_max", "gas_slope", "gas_delta_baseline", "gas_do_ratio",
            "temp_last", "temp_delta_long", "temp_slope_long", "hum_delta_long"),
    "fuite_gaz": ("gas_mean", "gas_max", "gas_min", "gas_slope", "gas_delta_baseline", "gas_do_ratio",
                  "temp_delta_long", "temp_slope_long"),
    "inondation": ("hum_last", "hum_delta_long"),
    "presence": ("pir_ratio", "cam_ratio"),
}

# Sessions simulées : (situation, nombre de sessions par unité de --sessions)
KINDS: tuple[tuple[str, int], ...] = (
    (NEGATIVE, 3), ("presence", 1), ("fuite_gaz", 1), ("feu", 1), ("inondation", 1),
    ("presence+fuite_gaz", 1), ("presence+feu", 1), ("presence+inondation", 1),
)

# Début de situation non étiqueté : le temps que les capteurs réagissent (s)
ONSET_S = {"presence": 3.0, "fuite_gaz": 12.0, "feu": 25.0, "inondation": 20.0}
RECOVERY_S = 60.0
"""Après une situation, fenêtres ignorées tant que la fenêtre longue (60 s) contient la fin."""

TICK_S = 0.5
KEEP_EVERY = 4
"""Une fenêtre sur 4 (toutes les 2 s) : les fenêtres voisines sont presque identiques."""


@dataclass
class Window:
    features: dict[str, float]
    label: str
    session_id: str


@dataclass
class Perturbations:
    """Pièges d'une session « aucune » : rien de tout cela n'est une alerte."""

    gas_puffs: list[tuple[float, float, float]] = field(default_factory=list)  # (début, durée, excès)
    heater_c_per_min: float = 0.0
    hum_bump: tuple[float, float, float] | None = None  # (début, durée de montée, amplitude)
    pir_glitch_rate: float = 0.0  # probabilité qu'un message ait pir=1 par erreur

    def apply(self, sim: RoomSimulator, base: dict[str, float], t: float, t0: float) -> None:
        rel = t - t0
        sim.base_gas = base["gas"] + sum(x for start, dur, x in self.gas_puffs if start <= rel < start + dur)
        sim.base_temp = base["temp"] + self.heater_c_per_min * max(0.0, rel) / 60.0
        if self.hum_bump:
            start, rise, amp = self.hum_bump
            k = min(1.0, max(0.0, (rel - start) / rise)) if rel < start + 2 * rise else \
                max(0.0, 1.0 - (rel - start - 2 * rise) / (2 * rise))
            sim.base_hum = base["hum"] + amp * k
        else:
            sim.base_hum = base["hum"]


def random_room(rng: random.Random, kind: str) -> tuple[RoomSimulator, dict[str, float]]:
    parts = label_parts(kind)
    base_gas = rng.uniform(3, 40) if rng.random() < 0.15 else min(180.0, max(40.0, rng.gauss(100, 18)))
    sim = RoomSimulator(seed=rng.randrange(1 << 30), base_gas=base_gas, base_temp=rng.uniform(17, 27),
                        base_hum=rng.uniform(30, 78))
    sim.leak_gas = rng.uniform(120, 600)
    sim.fire_gas = rng.uniform(150, 500)
    sim.fire_rate_c_per_min = rng.uniform(1.5, 6.0)
    sim.flood_hum = rng.uniform(88, 99.5)
    room = {
        "rate": rng.uniform(3, 8),
        "camera_rate": rng.uniform(0.5, 2.0) if rng.random() < 0.5 else 0.0,
        "send_do": float(rng.random() < 0.5),
        # DHT22 absent : seulement là où l'alerte ne dépend pas de lui
        "dht_missing": float(rng.random() < 0.06 and not parts & {"feu", "inondation"}),
    }
    return sim, room


def random_perturbations(rng: random.Random, base_hum: float, duration: float) -> Perturbations:
    p = Perturbations()
    for _ in range(rng.choice((0, 1, 2, 3))):
        p.gas_puffs.append((rng.uniform(0, duration), rng.uniform(2, 8), rng.uniform(30, 100)))
    if rng.random() < 0.3:
        p.heater_c_per_min = rng.uniform(0.05, 0.35)
    if rng.random() < 0.4:
        amp = min(rng.uniform(4, 12), 84 - base_hum)
        if amp > 0:
            p.hum_bump = (rng.uniform(0, duration / 2), rng.uniform(30, 120), amp)
    if rng.random() < 0.3:
        p.pir_glitch_rate = rng.uniform(0.002, 0.02)
    return p


def settings() -> Settings:
    return Settings(_env_file=None, warmup_seconds=5, database_url=None, mqtt_result_topic=None)


def simulate_session(rng: random.Random, kind: str, index: int, cfg: Settings) -> list[Window]:
    """Une session : normal → situation → normal. Retourne les fenêtres étiquetées gardées."""
    sim, room = random_room(rng, kind)
    base = {"gas": sim.base_gas, "temp": sim.base_temp, "hum": sim.base_hum}
    pre, mid, post = rng.uniform(100, 160), rng.uniform(60, 180), rng.uniform(60, 120)
    positive = kind != NEGATIVE
    perturb = random_perturbations(rng, base["hum"], pre + mid + post) if not positive else Perturbations()
    session_id = f"sim-{index:04d}-{kind}"
    dev = DevicePipeline(session_id, cfg)
    predictor = RuleBasedPredictor()  # pilote seulement le gel de la baseline gaz pendant une alerte

    t0 = 1_800_000_000.0 + index * 10_000.0
    t_mid, t_post, t_end = t0 + pre, t0 + pre + mid, t0 + pre + mid + post
    next_sensor, next_cam, next_tick = t0, t0, t0 + TICK_S
    onset = max((ONSET_S.get(p, 0.0) for p in label_parts(kind)), default=0.0)
    out, n_tick = [], 0
    while next_tick <= t_end:
        t = min(next_sensor, next_cam, next_tick)
        scenario = kind if t_mid <= t < t_post and positive else "normal"
        if room["dht_missing"]:
            scenario += "+dht_nan"
        if t == next_sensor:
            perturb.apply(sim, base, t, t0)
            msg = sim.sensor(t, scenario)
            if msg is not None:
                if not room["send_do"]:
                    msg.pop("gas_do")
                if perturb.pir_glitch_rate and rng.random() < perturb.pir_glitch_rate:
                    msg["pir"] = 1
                dev.add_sensor(SensorReading.from_message(session_id, t, SensorMessage.model_validate(msg)))
            next_sensor = t + rng.uniform(0.6, 1.4) / room["rate"]
        elif t == next_cam:
            if room["camera_rate"]:
                cam = sim.camera(t, scenario)
                if cam is not None:
                    dev.add_camera(CameraEvent(session_id, t, cam["ts"], cam["person"]))
                next_cam = t + 1.0 / room["camera_rate"]
            else:
                next_cam = math.inf
        else:
            result = dev.tick(t, predictor, lambda exc: None)
            next_tick = t + TICK_S
            n_tick += 1
            if result.device_state != "ok" or n_tick % KEEP_EVERY:
                continue
            if t_mid + onset <= t < t_post and positive:
                label = kind
            elif t < t_mid or not positive:
                label = NEGATIVE
            elif t >= t_post + RECOVERY_S and sim.gas_excess < 20 and sim.temp_excess < 0.5 and sim.hum_excess < 6:
                label = NEGATIVE
            else:
                continue  # transition : étiquette ambiguë
            out.append(Window(dict(result.features), label, session_id))
    return out


def load_real(path: Path) -> list[Window]:
    if not path.is_file():
        return []
    windows = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            feats = {n: float(row[n]) if row.get(n) not in (None, "") else math.nan for n in FEATURE_NAMES}
            windows.append(Window(feats, row["label"], f"real-{row['session_id']}"))
    return windows[::KEEP_EVERY // 2]


def binary(label: str, alert_type: str) -> str:
    return alert_type if alert_type in label_parts(label) else NEGATIVE


def to_table(windows: list[Window], alert_type: str):
    from Orange.data import ContinuousVariable, DiscreteVariable, Domain, StringVariable, Table

    names = MODEL_FEATURES[alert_type]
    domain = Domain([ContinuousVariable(n) for n in names],
                    DiscreteVariable("label", values=(NEGATIVE, alert_type)),
                    metas=[StringVariable("session_id")])
    x = np.array([[w.features.get(n, math.nan) for n in names] for w in windows], dtype=float)
    y = np.array([float(binary(w.label, alert_type) == alert_type) for w in windows])
    metas = np.array([[w.session_id] for w in windows], dtype=object)
    return Table.from_numpy(domain, x, y, metas)


def learner():
    from Orange.classification import RandomForestLearner

    return RandomForestLearner(n_estimators=60, max_depth=14, min_samples_leaf=8,
                               class_weight="balanced", random_state=0)


def write_csv(windows: list[Window], alert_type: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([*FEATURE_NAMES, "cD#label", "mS#session_id"])
        for win in windows:
            w.writerow([*("" if math.isnan(v := win.features.get(n, math.nan)) else v for n in FEATURE_NAMES),
                        binary(win.label, alert_type), win.session_id])


def evaluate(windows: list[Window], alert_type: str, rng: random.Random) -> str:
    """Validation par session (jamais les mêmes sessions en entraînement et en test)."""
    from sklearn.metrics import roc_auc_score

    sessions = sorted({w.session_id for w in windows})
    test_ids = set(rng.sample(sessions, k=len(sessions) // 4))
    train = [w for w in windows if w.session_id not in test_ids]
    test = [w for w in windows if w.session_id in test_ids]
    model = learner()(to_table(train, alert_type))
    table = to_table(test, alert_type)
    proba = model(table, model.Probs)[:, 1]
    y = table.Y.astype(int)
    pred = proba >= 0.6  # ALERT_THRESHOLD_ON
    real = np.array([w.session_id.startswith("real-") for w in test])
    sim_neg = (y == 0) & ~real
    parts = [
        f"AUC {roc_auc_score(y, proba):.3f}",
        f"rappel {pred[y == 1].mean():.1%}",
        f"faux positifs simulés {pred[sim_neg].mean():.2%}",
    ]
    if real.any():
        parts.append(f"faux positifs réels {pred[real].mean():.2%}")
    return ", ".join(parts)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ROOT / "models", help="dossier des .pkcls")
    p.add_argument("--sessions", type=int, default=30, help="sessions simulées par situation")
    p.add_argument("--real", type=Path, nargs="*",
                   default=[ROOT / "exports/temoins_aucune/features/temoins_aucune_toutes_plages.csv"],
                   help="CSV de fenêtres réelles étiquetées (export_dataset.py), ajoutés à l'entraînement")
    p.add_argument("--csv-dir", type=Path, default=ROOT / "exports/synthetique")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--no-eval", action="store_true")
    args = p.parse_args()

    rng = random.Random(args.seed)
    cfg = settings()
    windows: list[Window] = []
    index = 0
    for kind, weight in KINDS:
        n_before = len(windows)
        for _ in range(weight * args.sessions):
            windows += simulate_session(rng, kind, index, cfg)
            index += 1
        print(f"simulé  {kind:<20} {weight * args.sessions:>4} sessions, {len(windows) - n_before:>6} fenêtres")
    for path in args.real:
        real = load_real(path)
        print(f"réel    {path.name:<20} {len({w.session_id for w in real}):>4} sessions, {len(real):>6} fenêtres"
              if real else f"réel    {path} introuvable, ignoré")
        windows += real

    args.out.mkdir(parents=True, exist_ok=True)
    for alert_type in ALERT_TYPES:
        if not args.no_eval:
            print(f"{alert_type:<11} validation : {evaluate(windows, alert_type, random.Random(args.seed))}")
        model = learner()(to_table(windows, alert_type))
        path = args.out / f"{alert_type}.pkcls"
        with path.open("wb") as fh:
            pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)  # comme le widget Save Model
        write_csv(windows, alert_type, args.csv_dir / f"{alert_type}.csv")
        print(f"{alert_type:<11} → {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} "
              f"({path.stat().st_size // 1024} Ko, colonnes : {', '.join(MODEL_FEATURES[alert_type])})")


if __name__ == "__main__":
    main()
