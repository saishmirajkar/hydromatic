"""Command-line entry points.

    python cli.py calibrate                 # refit calibration from data/reference_parts
    python cli.py estimate part.json        # cycle time + cost for a PartSpec JSON
    python cli.py extract drawing.pdf       # read a drawing with Claude, print PartSpec JSON
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hydroquote import (CostInputs, PartSpec, ReferencePart, calibrate, compute_cost,
                        estimate_with_calibration, load_calibration, load_machine,
                        load_reference_parts)
from hydroquote.calibration import save_calibration


def _load_part(path: Path) -> PartSpec:
    data = json.loads(path.read_text())
    if "part" in data:  # a reference-part file
        return ReferencePart.model_validate(data).part
    return PartSpec.model_validate(data)


def cmd_calibrate(args: argparse.Namespace) -> None:
    cal = calibrate(load_reference_parts(), load_machine(), rounding_step_min=args.rounding)
    save_calibration(cal)
    print(f"mode={cal.mode}  cut_factor={cal.cut_factor}  overhead={cal.overhead_min} min  "
          f"parts={cal.n_parts}")
    print(f"MAE {cal.mae_min} min | leave-one-out MAE {cal.loo_mae_min} min | "
          f"'just quote the average' LOO MAE {cal.baseline_loo_mae_min} min")
    for r in cal.residuals:
        print(f"  {r.description:30s} actual {r.actual_min:5.2f}  predicted {r.predicted_min:5.2f}  "
              f"(physics {r.physics_min:4.2f})")


def cmd_estimate(args: argparse.Namespace) -> None:
    machine, cal = load_machine(), load_calibration()
    part = _load_part(Path(args.part))
    est = estimate_with_calibration(part, machine, cal)
    for row in est.breakdown_rows():
        print(f"  {row['end']}  {row['operation']:34s} {row['tool']:14s} {row['seconds']:6.1f}s  {row['detail']}")
    print(f"physics {est.physics_min:.2f} min -> cycle time {est.cycle_time_min:.2f} min "
          f"(rounded {est.cycle_time_rounded_min})")
    cost = compute_cost(part, CostInputs(), est.cycle_time_rounded_min, machine.machining_rate_rs_per_min)
    print(f"Net Rs {cost.net:.2f}  S.P. Rs {cost.selling_price:.2f}  (bought-out items not included)")
    for w in part.warnings + est.warnings:
        print("WARNING:", w)


def cmd_extract(args: argparse.Namespace) -> None:
    from hydroquote.extraction import extract_part_from_drawing
    path = Path(args.drawing)
    part = extract_part_from_drawing(path.read_bytes(), path.name)
    print(part.model_dump_json(indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("--rounding", type=float, default=0.25, help="round cycle time to this step (min)")
    c.set_defaults(func=cmd_calibrate)
    e = sub.add_parser("estimate")
    e.add_argument("part")
    e.set_defaults(func=cmd_estimate)
    x = sub.add_parser("extract")
    x.add_argument("drawing")
    x.set_defaults(func=cmd_extract)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
