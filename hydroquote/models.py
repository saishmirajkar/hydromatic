"""Data models shared by extraction, cycle-time estimation and costing."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class FeatureType(str, Enum):
    """Machining features found on turned hydraulic fittings.

    Facing of both ends and the hex-bar to round transition are added
    automatically by the planner, so they are not listed here.
    """

    turn = "turn"                # external diameter turned from bar (spigot, thread OD, collar)
    taper_ext = "taper_ext"      # external taper / male 24 deg cone
    thread_ext = "thread_ext"    # external thread (BSP, metric, UNF ...)
    thread_int = "thread_int"    # internal thread
    groove = "groove"            # undercut, relief, O-ring or ED seal groove
    drill = "drill"              # drilled hole (through or blind)
    bore = "bore"                # internal bore / counterbore
    cone_24 = "cone_24"          # internal 24 deg tube cone (ISO 8434-1 / DIN 2353)
    chamfer = "chamfer"          # chamfer on a turned edge
    hex_chamfer = "hex_chamfer"  # 15/30 deg chamfer on hex corners


class Feature(BaseModel):
    end: Literal["A", "B"] = Field(
        description="Machining setup the feature is cut in. A = first end (usually the "
        "port/stud end), B = second end after flipping the part."
    )
    type: FeatureType
    dia: float = Field(description="Diameter in mm (major dia for threads, larger dia for tapers/cones).")
    length: float = Field(
        0.0,
        description="Axial length in mm (depth for drills/bores, width for grooves, "
        "thread length for threads). Chamfers may use 0.",
    )
    pitch: float = Field(0.0, description="Thread pitch in mm. Only for thread features, else 0.")
    qty: int = Field(1, ge=1, description="How many identical features.")
    note: str = Field("", description="Short free-text note, e.g. 'G1/4 BSP' or 'O-ring groove'.")


class PartSpec(BaseModel):
    """Everything the cycle-time engine and the cost sheet need about one part."""

    description: str = Field(description="Part description as written on the drawing, e.g. 'GE 12 X 3/8 - L - B'.")
    drawing_no: str = ""
    material: str = Field("CS", description="Raw material code used in the cost sheet (CS = carbon steel).")
    shape: Literal["S", "R"] = Field(
        "S", description="Bar shape: S = hexagon bar (cost sheet 'S'), R = round bar."
    )
    bar_size: float = Field(description="Hex across-flats (or round bar dia) in mm. Cost sheet column Y.")
    overall_length: float = Field(description="Overall finished length in mm. Cost sheet column L.")
    features: list[Feature] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Anything ambiguous or unreadable on the drawing that a person should check.",
    )


class ReferencePart(BaseModel):
    """A part with a known (quoted or measured) cycle time, used for calibration."""

    part: PartSpec
    actual_cycle_time_min: float
    source: str = ""
    use_for_calibration: bool = True


class CostInputs(BaseModel):
    """Inputs of one row of the 'Straight' cost sheet that do not come from the drawing."""

    quantity: float = 5
    raw_material_rate: float = 85.0     # K  Rs/kg
    caps: float = 0.0                   # Q
    crimp_n: float = 0.0                # R
    nut_red_n: float = 0.0              # S  (not part of Net in the original sheet)
    ferrule_wn: float = 0.0             # T  (not part of Net in the original sheet)
    cn: float = 0.0                     # U
    was: float = 0.0                    # V
    o_ring: float = 0.0                 # W
    sr_ring: float = 0.0                # X
    margin: float = 1.3                 # Z


class QuoteLine(BaseModel):
    part: PartSpec
    cost: CostInputs = Field(default_factory=CostInputs)
    cycle_time_min: float
    cycle_time_source: str = "estimated"   # "estimated" or "manual"
