"""Cycle-time (TL) estimation for turned hex fittings on the Smarturn.

The estimate is built bottom-up the way a process planner would do it:

1. every drawing feature is turned into a lathe operation on setup A or B,
2. cutting time is calculated from length, feed and spindle speed
   (cutting speed for the material, limited by the machine's max rpm),
3. non-cutting time is added per tool used (turret index, approach/retract)
   and per setup (load/unload, spindle start/stop),
4. the physics total is mapped to the shop's real cycle time with the
   calibration ``CT = cut_factor * physics_time + overhead``, fitted on parts
   whose cycle time is known (see ``calibration.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .machine import MachineProfile
from .models import Feature, FeatureType, PartSpec

HEX_CORNER_FACTOR = 1 / math.cos(math.radians(30))  # across-corners = AF * 1.1547
GROOVE_TOOL_WIDTH_MM = 2.0
CONE_HALF_ANGLE_DEG = 12.0  # 24 deg included cone


@dataclass
class OperationTime:
    end: str
    operation: str
    tool: str
    cut_s: float
    detail: str = ""


@dataclass
class CycleTimeEstimate:
    operations: list[OperationTime] = field(default_factory=list)
    noncut: list[OperationTime] = field(default_factory=list)
    cut_factor: float = 1.0
    overhead_min: float = 0.0
    rounding_step_min: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def cutting_s(self) -> float:
        return sum(op.cut_s for op in self.operations)

    @property
    def noncut_s(self) -> float:
        return sum(op.cut_s for op in self.noncut)

    @property
    def physics_min(self) -> float:
        """Machine time from first principles, before calibration."""
        return (self.cutting_s + self.noncut_s) / 60.0

    @property
    def cycle_time_min(self) -> float:
        """Calibrated cycle time in minutes per piece (the cost sheet 'Cycle Time')."""
        return self.cut_factor * self.physics_min + self.overhead_min

    @property
    def cycle_time_rounded_min(self) -> float:
        if not self.rounding_step_min:
            return self.cycle_time_min
        step = self.rounding_step_min
        return round(round(self.cycle_time_min / step) * step, 4)

    def breakdown_rows(self) -> list[dict]:
        return [
            {"end": o.end, "operation": o.operation, "tool": o.tool,
             "seconds": round(o.cut_s, 1), "detail": o.detail}
            for o in self.operations + self.noncut
        ]


def _rpm(dia_mm: float, vc_m_min: float, max_rpm: float) -> float:
    dia_mm = max(dia_mm, 1.0)
    return min(1000.0 * vc_m_min / (math.pi * dia_mm), max_rpm)


def _cut_seconds(length_mm: float, feed_mm_rev: float, rpm: float) -> float:
    return 60.0 * length_mm / (feed_mm_rev * rpm)


def bar_envelope_dia(part: PartSpec) -> float:
    """Largest diameter of the raw bar (hex across-corners or round bar dia)."""
    return part.bar_size * HEX_CORNER_FACTOR if part.shape == "S" else part.bar_size


def _parent_od(feature: Feature, end_turns: list[Feature], envelope: float) -> float:
    """Diameter the feature is cut from: the next larger turned dia covering it, else the bar."""
    candidates = [
        t.dia for t in end_turns
        if t is not feature and t.dia > feature.dia and t.length >= feature.length
    ]
    return min(candidates) if candidates else envelope


def _pre_hole(feature: Feature, part: PartSpec) -> float:
    """Diameter of the hole an internal feature is opened up from."""
    candidates = [
        f.dia for f in part.features
        if f is not feature and f.dia < feature.dia
        and (f.type == FeatureType.drill
             or (f.end == feature.end and f.type in (FeatureType.bore, FeatureType.cone_24)))
    ]
    return max(candidates) if candidates else 0.0


def _tool_for(feature: Feature) -> str:
    return {
        FeatureType.turn: "OD turning",
        FeatureType.taper_ext: "OD turning",
        FeatureType.chamfer: "OD turning",
        FeatureType.hex_chamfer: "OD turning",
        FeatureType.groove: "grooving",
        FeatureType.thread_ext: "OD threading",
        FeatureType.thread_int: "ID threading",
        FeatureType.bore: "boring bar",
        FeatureType.cone_24: "boring bar",
        FeatureType.drill: f"drill {feature.dia:g}",
    }[feature.type]


def estimate_cycle_time(
    part: PartSpec,
    machine: MachineProfile,
    cut_factor: float = 1.0,
    overhead_min: float = 0.0,
    rounding_step_min: float = 0.0,
) -> CycleTimeEstimate:
    cd = machine.cutting(part.material)
    max_rpm = machine.max_rpm
    envelope = bar_envelope_dia(part)
    est = CycleTimeEstimate(cut_factor=cut_factor, overhead_min=overhead_min,
                            rounding_step_min=rounding_step_min)
    ops = est.operations

    ends = ["A", "B"][: max(1, machine.setups_per_part)]
    for end in ends:
        feats = [f for f in part.features if f.end == end]
        turns = [f for f in feats if f.type in (FeatureType.turn, FeatureType.taper_ext)]

        # Facing the sawn billet face (always done, every setup).
        face = cd["face"]
        passes = max(1, math.ceil(machine.facing_stock_per_side_mm / face["doc"]))
        rpm = _rpm(envelope / 2, face["vc"], max_rpm)  # CSS: use mean diameter
        t = passes * _cut_seconds(envelope / 2, face["feed"], rpm)
        ops.append(OperationTime(end, "face end", "OD turning", t,
                                 f"{passes} pass(es) over Ø{envelope:.1f}"))

        for f in feats:
            tool = _tool_for(f)
            q = f.qty
            if f.type in (FeatureType.turn, FeatureType.taper_ext):
                tc = cd["turn"]
                parent = _parent_od(f, turns, envelope)
                stock = max(0.0, (parent - f.dia) / 2)
                rough_passes = math.ceil(stock / tc["doc"]) if stock > 0 else 0
                rpm = _rpm((parent + f.dia) / 2, tc["vc"], max_rpm)
                t = rough_passes * _cut_seconds(f.length + 1.0, tc["feed"], rpm)
                t += _cut_seconds(f.length, tc["finish_feed"], _rpm(f.dia, tc["vc"], max_rpm))
                ops.append(OperationTime(end, f"turn Ø{f.dia:g} x {f.length:g}", tool, q * t,
                                         f"{rough_passes} rough + 1 finish, from Ø{parent:.1f}"))
            elif f.type in (FeatureType.thread_ext, FeatureType.thread_int):
                th = cd["thread"]
                pitch = f.pitch or 1.5
                depth = (0.6134 if f.type == FeatureType.thread_ext else 0.5413) * pitch
                passes = math.ceil(depth / th["infeed_per_pass"]) + int(th["spring_passes"])
                rpm = _rpm(f.dia, th["vc"], max_rpm)
                run = f.length + 2 * pitch + 2.0  # approach + run-out
                t = passes * (_cut_seconds(run, pitch, rpm) + th["return_s_per_pass"])
                ops.append(OperationTime(end, f"thread Ø{f.dia:g} x P{pitch:g} x {f.length:g}",
                                         tool, q * t, f"{passes} passes"))
            elif f.type == FeatureType.groove:
                g = cd["groove"]
                parent = _parent_od(f, turns, envelope)
                depth = max(0.3, (parent - f.dia) / 2)
                plunges = max(1, math.ceil(f.length / GROOVE_TOOL_WIDTH_MM))
                rpm = _rpm(f.dia, g["vc"], max_rpm)
                t = plunges * (_cut_seconds(depth, g["feed"], rpm) + 0.5)
                ops.append(OperationTime(end, f"groove Ø{f.dia:g} x {f.length:g}", tool, q * t,
                                         f"{plunges} plunge(s), {depth:.2f} deep"))
            elif f.type == FeatureType.drill:
                d = cd["drill"]
                feed = d["feed_per_mm_dia"] * f.dia
                rpm = _rpm(f.dia, d["vc"], max_rpm)
                travel = f.length + 0.3 * f.dia + 2.0  # point + approach
                t = _cut_seconds(travel, feed, rpm)
                if f.length / f.dia > d["peck_ratio"]:
                    t *= d["peck_penalty"]
                ops.append(OperationTime(end, f"drill Ø{f.dia:g} x {f.length:g}", tool, q * t,
                                         f"f={feed:.3f} mm/rev"))
            elif f.type == FeatureType.bore:
                b = cd["bore"]
                pre = _pre_hole(f, part)
                stock = max(0.0, (f.dia - pre) / 2)
                passes = max(1, math.ceil(stock / b["doc"]))
                rpm = _rpm(f.dia, b["vc"], max_rpm)
                t = passes * _cut_seconds(f.length + 1.0, b["feed"], rpm)
                ops.append(OperationTime(end, f"bore Ø{f.dia:g} x {f.length:g}", tool, q * t,
                                         f"{passes} pass(es) from Ø{pre:g}"))
            elif f.type == FeatureType.cone_24:
                b = cd["bore"]
                pre = _pre_hole(f, part)
                radial = max(0.2, (f.dia - pre) / 2)
                cone_len = radial / math.tan(math.radians(CONE_HALF_ANGLE_DEG))
                passes = max(1, math.ceil(radial / b["doc"])) + 1
                rpm = _rpm(f.dia, b["vc"], max_rpm)
                t = passes * _cut_seconds(cone_len + 0.5, b["feed"], rpm)
                ops.append(OperationTime(end, f"24° cone Ø{f.dia:g}", tool, q * t,
                                         f"{passes} passes, {cone_len:.1f} long"))
            elif f.type == FeatureType.chamfer:
                ops.append(OperationTime(end, f"chamfer Ø{f.dia:g}", tool,
                                         q * cd["chamfer"]["seconds_each"]))
            elif f.type == FeatureType.hex_chamfer:
                ops.append(OperationTime(end, "hex chamfer", tool,
                                         q * cd["hex_chamfer"]["seconds_each"]))

        tools = {"OD turning"} | {_tool_for(f) for f in feats}
        est.noncut.append(OperationTime(
            end, "tool changes + approach", f"{len(tools)} tools",
            len(tools) * (machine.tool_index_s + machine.approach_retract_s_per_tool),
            ", ".join(sorted(tools))))
        est.noncut.append(OperationTime(
            end, "load / unload + spindle", "-",
            machine.load_unload_s_per_setup + machine.spindle_start_stop_s_per_setup))
        if len(tools) > machine.turret_stations:
            est.warnings.append(
                f"Setup {end} needs {len(tools)} tools but the turret has {machine.turret_stations} stations.")

    return est
