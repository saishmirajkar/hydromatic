"""The 'Straight' cost sheet, reproduced formula for formula.

Column letters refer to the original workbook (sheet 'Straight'):

    H  Hex          = Y / 10                        (bar size in cm)
    I  Tl           = (L + 6) / 10                  (billet length in cm, 6 mm facing allowance)
    J  RM weight    = 0.8666 * 7.85 * H * H * I / 1000   (kg, hex bar)
    L  RM cost      = J * K
    M  Cutting      = Y * 0.06
    O  Machining    = N * 3.75                      (N = cycle time, min)
    P  Plating      = J * 0.6 * 24
    Y  Net          = L + M + O + P + Q + R + U + V + W + X
    AA S.P.         = Net * Mgn

Note: columns S (NUT|RED N) and T (FERULE/WN) are filled in the original sheet
but are *not* part of Net. That behaviour is kept here on purpose so results
match the workbook; see ``include_nut_ferrule`` to change it.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from .models import CostInputs, PartSpec

STEEL_DENSITY = 7.85          # g/cm3
HEX_AREA_FACTOR = 0.8666      # area = 0.8666 * AF^2 (as used in the sheet)
FACING_ALLOWANCE_MM = 6.0
CUTTING_RS_PER_MM = 0.06
PLATING_FACTOR = 0.6 * 24
DEFAULT_MACHINING_RATE = 3.75  # Rs per minute


class CostBreakdown(BaseModel):
    hex_cm: float
    tl_cm: float
    rm_weight_kg: float
    rm_cost: float
    cutting: float
    cycle_time_min: float
    machining: float
    plating: float
    bought_out: float
    net: float
    selling_price: float
    total_value: float


def compute_cost(
    part: PartSpec,
    cost: CostInputs,
    cycle_time_min: float,
    machining_rate: float = DEFAULT_MACHINING_RATE,
    include_nut_ferrule: bool = False,
) -> CostBreakdown:
    y, length = part.bar_size, part.overall_length
    hex_cm = y / 10
    tl_cm = (length + FACING_ALLOWANCE_MM) / 10
    if part.shape == "S":
        weight = HEX_AREA_FACTOR * STEEL_DENSITY * hex_cm * hex_cm * tl_cm / 1000
    else:  # round bar (not in the original sheet): pi/4 * d^2
        weight = math.pi / 4 * STEEL_DENSITY * hex_cm * hex_cm * tl_cm / 1000
    rm_cost = weight * cost.raw_material_rate
    cutting = y * CUTTING_RS_PER_MM
    machining = cycle_time_min * machining_rate
    plating = weight * PLATING_FACTOR
    bought_out = cost.caps + cost.crimp_n + cost.cn + cost.was + cost.o_ring + cost.sr_ring
    if include_nut_ferrule:
        bought_out += cost.nut_red_n + cost.ferrule_wn
    net = rm_cost + cutting + machining + plating + bought_out
    sp = net * cost.margin
    return CostBreakdown(
        hex_cm=hex_cm, tl_cm=tl_cm, rm_weight_kg=weight, rm_cost=rm_cost, cutting=cutting,
        cycle_time_min=cycle_time_min, machining=machining, plating=plating,
        bought_out=bought_out, net=net, selling_price=sp, total_value=sp * cost.quantity,
    )
