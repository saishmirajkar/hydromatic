"""The browser engine (web/engine.js) must give the same answers as the Python package."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hydroquote import (CostInputs, calibrate, compute_cost, estimate_cycle_time, load_machine,
                        load_reference_parts)

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

SCRIPT = """
const HQ = require(process.argv[1]);
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const parts = input.refs.map(r => ({...r, part: HQ.normalizePart(r.part)}));
const est = parts.map(r => HQ.estimateCycleTime(r.part, input.machine));
const cal = HQ.calibrate(parts, input.machine, 0.25);
const cost = parts.map(r => HQ.computeCost(r.part, {caps: 4, o_ring: 7, nut_red_n: 20}, 4.5, 3.75, false));
process.stdout.write(JSON.stringify({
  physics: est.map(e => e.physics_min),
  ops: est.map(e => [...e.operations, ...e.noncut].map(o => [o.operation, o.cut_s])),
  cal, cost: cost.map(c => [c.rm_weight_kg, c.net, c.selling_price]),
  defaultMachine: HQ.DEFAULT_MACHINE,
}));
"""


@pytest.fixture(scope="module")
def js():
    if not NODE:
        pytest.skip("node not installed")
    machine = load_machine()
    refs = [json.loads(r.model_dump_json()) for r in load_reference_parts()]
    out = subprocess.run(
        [NODE, "-e", SCRIPT, str(ROOT / "web" / "engine.js")],
        input=json.dumps({"refs": refs, "machine": machine.model_dump()}),
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_physics_and_operations_match(js):
    machine = load_machine()
    for i, ref in enumerate(load_reference_parts()):
        est = estimate_cycle_time(ref.part, machine)
        assert js["physics"][i] == pytest.approx(est.physics_min, rel=1e-9)
        py_ops = [(o.operation, o.cut_s) for o in est.operations + est.noncut]
        assert [o[0] for o in js["ops"][i]] == [o[0] for o in py_ops]
        assert [o[1] for o in js["ops"][i]] == pytest.approx([o[1] for o in py_ops], rel=1e-9)


def test_calibration_matches(js):
    cal = calibrate(load_reference_parts(), load_machine(), 0.25)
    for key in ("cut_factor", "overhead_min", "mode", "n_parts", "mae_min", "loo_mae_min",
                "baseline_loo_mae_min"):
        assert js["cal"][key] == getattr(cal, key), key


def test_cost_matches(js):
    for i, ref in enumerate(load_reference_parts()):
        c = compute_cost(ref.part, CostInputs(caps=4, o_ring=7, nut_red_n=20), 4.5)
        assert js["cost"][i] == pytest.approx([c.rm_weight_kg, c.net, c.selling_price], rel=1e-12)


def test_default_machine_matches_profile(js):
    assert js["defaultMachine"] == json.loads((ROOT / "data" / "machine_profile.json").read_text())
