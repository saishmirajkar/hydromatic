"""Write quotes in the 'Straight' cost-sheet layout (with live Excel formulas)."""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .cycle_time import CycleTimeEstimate
from .models import QuoteLine

HEADERS = [
    None, "Description", "Quantity", "RM", "Shape", "Y", "L", "Hex", "Tl",
    "Raw Material Weight-Str", "Raw Material Rate", "Raw Mmaterial Rate", "Cutting Price",
    "Cycle Time", "Machining Rate", "Plating", "Caps", "Crimp N", "NUT|RED N", "FERULE/WN",
    "CN", "Was", "O Ring", "SR Ring", "Net", "Mgn", "S.P.",
]
ESTIMATE_FILL = PatternFill("solid", fgColor="FFF2CC")


def _row_formulas(r: int, machining_rate: float, round_bar: bool) -> dict[str, str]:
    area = "PI()/4" if round_bar else "0.8666"
    return {
        "H": f"=+F{r}/10",
        "I": f"=(+G{r}+6)/10",
        "J": f"={area}*7.85*H{r}*H{r}*I{r}/1000",
        "L": f"=+J{r}*K{r}",
        "M": f"=+F{r}*0.06",
        "O": f"=+N{r}*{machining_rate:g}",
        "P": f"=(J{r}*0.6)*24",
        "Y": f"=+L{r}+M{r}+O{r}+P{r}+Q{r}+R{r}+U{r}+V{r}+W{r}+X{r}",
        "AA": f"=+Y{r}*Z{r}",
    }


def build_straight_workbook(
    lines: list[QuoteLine],
    estimates: list[CycleTimeEstimate | None] | None = None,
    machining_rate: float = 3.75,
) -> bytes:
    """Return an .xlsx (bytes) with a 'Straight' sheet and a 'TL breakdown' sheet."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Straight"
    for c, h in enumerate(HEADERS, start=1):
        if h is not None:
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(wrap_text=True)

    for i, line in enumerate(lines, start=1):
        r = i + 1
        p, k = line.part, line.cost
        values = {
            "A": i, "B": p.description, "C": k.quantity, "D": p.material, "E": p.shape,
            "F": p.bar_size, "G": p.overall_length, "K": k.raw_material_rate,
            "N": round(line.cycle_time_min, 2), "Q": k.caps, "R": k.crimp_n, "S": k.nut_red_n,
            "T": k.ferrule_wn, "U": k.cn, "V": k.was, "W": k.o_ring, "X": k.sr_ring, "Z": k.margin,
        }
        values.update(_row_formulas(r, machining_rate, p.shape == "R"))
        for col, v in values.items():
            ws[f"{col}{r}"] = v
        if line.cycle_time_source == "estimated":
            ws[f"N{r}"].fill = ESTIMATE_FILL

    for c in range(1, len(HEADERS) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 28 if c == 2 else 11
    ws.freeze_panes = "C2"

    bd = wb.create_sheet("TL breakdown")
    bd.append(["Part", "End", "Operation", "Tool", "Seconds", "Detail"])
    for cell in bd[1]:
        cell.font = Font(bold=True)
    for line, est in zip(lines, estimates or [None] * len(lines)):
        if est is None:
            bd.append([line.part.description, "", "cycle time entered manually", "", "", ""])
            continue
        for row in est.breakdown_rows():
            bd.append([line.part.description, row["end"], row["operation"], row["tool"],
                       row["seconds"], row["detail"]])
        bd.append([line.part.description, "", "physics machine time (min)", "", "",
                   round(est.physics_min, 3)])
        bd.append([line.part.description, "", "calibrated cycle time (min)", "", "",
                   f"{est.cut_factor:g} x physics + {est.overhead_min:g} = {est.cycle_time_min:.2f}"])
        bd.append([])
    for col, w in zip("ABCDEF", (28, 5, 34, 16, 9, 50)):
        bd.column_dimensions[col].width = w

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read_straight_rows(data: bytes) -> list[dict]:
    """Read description / Y / L / cycle time rows from an existing 'Straight' sheet."""
    wb = load_workbook(BytesIO(data), data_only=False)
    ws = wb["Straight"] if "Straight" in wb.sheetnames else wb.active
    rows = []
    for r in range(2, ws.max_row + 1):
        desc = ws[f"B{r}"].value
        if not desc:
            continue
        rows.append({
            "description": str(desc).strip(), "quantity": ws[f"C{r}"].value,
            "material": ws[f"D{r}"].value, "shape": ws[f"E{r}"].value,
            "bar_size": ws[f"F{r}"].value, "overall_length": ws[f"G{r}"].value,
            "cycle_time_min": ws[f"N{r}"].value,
        })
    return rows
