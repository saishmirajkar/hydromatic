"""Fit the physics model to the shop's known cycle times.

Model:  cycle_time = cut_factor * physics_time + overhead_min

* ``physics_time`` is the bottom-up machine time from ``cycle_time.py``.
* ``cut_factor`` absorbs everything the physics leaves out uniformly
  (real feeds/speeds used on the floor, operator efficiency, deburring,
  in-process inspection, tool wear allowance ...).
* ``overhead_min`` is a fixed per-piece allowance.

With few or noisy reference parts a two-parameter fit is unstable (the slope
can even go negative), so below ``MIN_POINTS_FOR_TWO_PARAMS`` reference parts,
or whenever the two-parameter fit is not physically sensible, only
``cut_factor`` is fitted (overhead fixed at 0).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from .cycle_time import estimate_cycle_time
from .machine import DATA_DIR, MachineProfile
from .models import ReferencePart

REFERENCE_DIR = DATA_DIR / "reference_parts"
CALIBRATION_PATH = DATA_DIR / "calibration.json"
MIN_POINTS_FOR_TWO_PARAMS = 8


class CalibrationResidual(BaseModel):
    description: str
    actual_min: float
    physics_min: float
    predicted_min: float
    loo_predicted_min: float | None = None


class Calibration(BaseModel):
    cut_factor: float = 2.0
    overhead_min: float = 0.0
    rounding_step_min: float = 0.25
    mode: str = "default"
    n_parts: int = 0
    mae_min: float | None = None
    loo_mae_min: float | None = None
    # Leave-one-out error of simply quoting the average of the other parts.
    # The physics model is only adding value when loo_mae_min beats this.
    baseline_loo_mae_min: float | None = None
    fitted_on: str = ""
    residuals: list[CalibrationResidual] = Field(default_factory=list)


def load_reference_parts(directory: Path = REFERENCE_DIR) -> list[ReferencePart]:
    return [
        ReferencePart.model_validate_json(p.read_text())
        for p in sorted(Path(directory).glob("*.json"))
    ]


def save_reference_part(ref: ReferencePart, directory: Path = REFERENCE_DIR) -> Path:
    safe = "".join(c if c.isalnum() or c in "-." else "_" for c in ref.part.description).strip("_")
    path = Path(directory) / f"{safe or 'part'}.json"
    path.write_text(ref.model_dump_json(indent=2) + "\n")
    return path


def _fit(physics: np.ndarray, actual: np.ndarray) -> tuple[float, float, str]:
    if len(physics) >= MIN_POINTS_FOR_TWO_PARAMS:
        a = np.column_stack([physics, np.ones_like(physics)])
        (k, h), *_ = np.linalg.lstsq(a, actual, rcond=None)
        if k > 0 and h >= 0:
            return float(k), float(h), "factor + overhead"
    k = float(physics @ actual / (physics @ physics))
    return k, 0.0, "factor only"


def calibrate(
    refs: list[ReferencePart],
    machine: MachineProfile,
    rounding_step_min: float = 0.25,
) -> Calibration:
    refs = [r for r in refs if r.use_for_calibration]
    if not refs:
        return Calibration(rounding_step_min=rounding_step_min)

    physics = np.array([estimate_cycle_time(r.part, machine).physics_min for r in refs])
    actual = np.array([r.actual_cycle_time_min for r in refs])
    k, h, mode = _fit(physics, actual)

    residuals = []
    loo_errors = []
    baseline_errors = []
    for i, r in enumerate(refs):
        pred = k * physics[i] + h
        loo = None
        if len(refs) > 1:
            mask = np.arange(len(refs)) != i
            k_i, h_i, _ = _fit(physics[mask], actual[mask])
            loo = k_i * physics[i] + h_i
            loo_errors.append(abs(loo - actual[i]))
            baseline_errors.append(abs(actual[mask].mean() - actual[i]))
        residuals.append(CalibrationResidual(
            description=r.part.description, actual_min=float(actual[i]),
            physics_min=round(float(physics[i]), 3), predicted_min=round(float(pred), 3),
            loo_predicted_min=None if loo is None else round(float(loo), 3)))

    return Calibration(
        cut_factor=round(k, 4), overhead_min=round(h, 4), rounding_step_min=rounding_step_min,
        mode=mode, n_parts=len(refs),
        mae_min=round(float(np.mean(np.abs(k * physics + h - actual))), 3),
        loo_mae_min=round(float(np.mean(loo_errors)), 3) if loo_errors else None,
        baseline_loo_mae_min=round(float(np.mean(baseline_errors)), 3) if baseline_errors else None,
        fitted_on=date.today().isoformat(), residuals=residuals,
    )


def load_calibration(path: Path = CALIBRATION_PATH) -> Calibration:
    path = Path(path)
    if not path.exists():
        return Calibration()
    return Calibration.model_validate(json.loads(path.read_text()))


def save_calibration(cal: Calibration, path: Path = CALIBRATION_PATH) -> None:
    Path(path).write_text(cal.model_dump_json(indent=2) + "\n")
