"""Machine profile: Smarturn limits, shop timings and cutting data per material."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MACHINE_PROFILE_PATH = DATA_DIR / "machine_profile.json"


class MachineProfile(BaseModel):
    name: str
    source: str = ""
    max_rpm: float
    spindle_power_kw: float
    turret_stations: int
    chuck_mm: float
    machining_rate_rs_per_min: float
    setups_per_part: int
    load_unload_s_per_setup: float
    spindle_start_stop_s_per_setup: float
    tool_index_s: float
    approach_retract_s_per_tool: float
    facing_stock_per_side_mm: float
    cutting_data: dict[str, dict[str, dict[str, float]]]

    def cutting(self, material: str) -> dict[str, dict[str, float]]:
        """Cutting data for a material, falling back to carbon steel."""
        return self.cutting_data.get(material.upper(), self.cutting_data["CS"])


def load_machine(path: Path = MACHINE_PROFILE_PATH) -> MachineProfile:
    return MachineProfile.model_validate(json.loads(Path(path).read_text()))


def save_machine(machine: MachineProfile, path: Path = MACHINE_PROFILE_PATH) -> None:
    Path(path).write_text(json.dumps(machine.model_dump(), indent=2) + "\n")
