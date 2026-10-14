import pytest

from hydroquote import (Calibration, Feature, PartSpec, calibrate, estimate_cycle_time,
                        estimate_with_calibration, load_machine, load_reference_parts)


@pytest.fixture(scope="module")
def machine():
    return load_machine()


def _simple_part(**kw):
    feats = kw.pop("features", [
        Feature(end="A", type="drill", dia=7, length=32),
        Feature(end="A", type="turn", dia=18, length=12),
        Feature(end="A", type="thread_ext", dia=18, length=9, pitch=1.5),
        Feature(end="B", type="turn", dia=18, length=12),
        Feature(end="B", type="thread_ext", dia=18, length=9, pitch=1.5),
    ])
    return PartSpec(description="test", bar_size=kw.pop("bar_size", 19),
                    overall_length=kw.pop("overall_length", 32), features=feats, **kw)


def test_reference_parts_give_plausible_machine_time(machine):
    for ref in load_reference_parts():
        est = estimate_cycle_time(ref.part, machine)
        assert 0.5 < est.physics_min < 6, ref.part.description
        assert est.cutting_s > 0 and est.noncut_s > 0


def test_more_features_take_longer(machine):
    base = estimate_cycle_time(_simple_part(), machine).physics_min
    extra = _simple_part()
    extra.features.append(Feature(end="B", type="groove", dia=15.7, length=3))
    extra.features.append(Feature(end="B", type="cone_24", dia=12.3))
    assert estimate_cycle_time(extra, machine).physics_min > base


def test_longer_thread_and_bigger_bar_take_longer(machine):
    base = estimate_cycle_time(_simple_part(), machine).physics_min
    longer = _simple_part(features=[
        Feature(end="A", type="drill", dia=7, length=60),
        Feature(end="A", type="turn", dia=18, length=30),
        Feature(end="A", type="thread_ext", dia=18, length=25, pitch=1.5),
        Feature(end="B", type="turn", dia=18, length=12),
        Feature(end="B", type="thread_ext", dia=18, length=9, pitch=1.5),
    ], overall_length=60)
    assert estimate_cycle_time(longer, machine).physics_min > base
    assert estimate_cycle_time(_simple_part(bar_size=27), machine).physics_min > base


def test_rpm_is_capped_at_machine_max(machine):
    tiny = _simple_part(features=[Feature(end="A", type="drill", dia=1.0, length=10)])
    est = estimate_cycle_time(tiny, machine)
    drill = next(o for o in est.operations if o.operation.startswith("drill"))
    # f = 0.015 mm/rev at 4500 rpm -> 67.5 mm/min; travel 12.3 mm, peck penalty 1.4
    assert drill.cut_s == pytest.approx(60 * 12.3 / (0.015 * 4500) * 1.4)


def test_calibration_applies_factor_overhead_and_rounding(machine):
    part = _simple_part()
    cal = Calibration(cut_factor=2.0, overhead_min=0.5, rounding_step_min=0.5)
    est = estimate_with_calibration(part, machine, cal)
    assert est.cycle_time_min == pytest.approx(2.0 * est.physics_min + 0.5)
    assert est.cycle_time_rounded_min % 0.5 == pytest.approx(0)


def test_calibrate_recovers_known_factor(machine):
    refs = load_reference_parts()
    for r in refs:
        r.actual_cycle_time_min = 3.0 * estimate_cycle_time(r.part, machine).physics_min
    cal = calibrate(refs, machine)
    assert cal.cut_factor == pytest.approx(3.0, rel=1e-3)
    assert cal.mae_min == pytest.approx(0, abs=1e-3)


def test_turret_overflow_warns(machine):
    feats = [Feature(end="A", type="drill", dia=d, length=5) for d in (3, 4, 5, 6, 7, 8, 9, 10)]
    est = estimate_cycle_time(_simple_part(features=feats), machine)
    assert any("turret" in w for w in est.warnings)
