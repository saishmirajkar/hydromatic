"""Hydromatik quotation estimator: drawing -> cycle time (TL) -> Straight cost sheet."""

from .calibration import Calibration, calibrate, load_calibration, load_reference_parts
from .costing import CostBreakdown, compute_cost
from .cycle_time import CycleTimeEstimate, estimate_cycle_time
from .machine import MachineProfile, load_machine
from .models import CostInputs, Feature, FeatureType, PartSpec, QuoteLine, ReferencePart


def estimate_with_calibration(part: PartSpec, machine: MachineProfile, cal: Calibration) -> CycleTimeEstimate:
    return estimate_cycle_time(part, machine, cal.cut_factor, cal.overhead_min, cal.rounding_step_min)


__all__ = [
    "Calibration", "CostBreakdown", "CostInputs", "CycleTimeEstimate", "Feature", "FeatureType",
    "MachineProfile", "PartSpec", "QuoteLine", "ReferencePart", "calibrate", "compute_cost",
    "estimate_cycle_time", "estimate_with_calibration", "load_calibration", "load_machine",
    "load_reference_parts",
]
