"""Check the data tools against the published example answers."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from aicopilot.adapters import DuckDBStore
from aicopilot.config import get_settings
from aicopilot.ingest import ingest
from aicopilot.queries import FieldQueries


def main() -> None:
    settings = get_settings()
    print("Loading", settings.data_dir)
    counts = ingest(settings.data_dir, settings.db_path)
    print(counts)
    field = FieldQueries(DuckDBStore(settings.db_path), settings.today)

    status = field.well_status("WELL-0001")
    print("Q1", status["producing"], round(status["delta_c"], 1), status["inspection_id"], status["inspector_name"], status["recorded_status_stale"])
    print("   reason:", status["reason"][:140])

    q2 = field.non_producing("Northeast Block 8")
    print("Q2", [(w["asset_id"], w["delta_c"], w["reason"][:80]) for w in q2["wells"]])

    q3 = field.timeseries("WELL-0001", 13)
    print("Q3 points", len(q3["points"]), "down", q3["not_producing"])

    q4 = field.spills_for_well("WELL-0001")
    print("Q4", q4["count"], q4["total_bbl"], [s["spill_date"] for s in q4["spills"]])

    q5 = field.downtime_days("WELL-0001", 2025)
    print("Q5", q5["days"], [i["inspected_on"] for i in q5["inspections"]])

    q6 = field.top_downtime("Northeast Block 7", date(2026, 6, 16), date(2026, 10, 16), 3)
    print("Q6", q6["wells"], "next", q6["next"])

    q7 = field.spill_map("Northeast Block 8", 90)
    print("Q7", [(p["asset_id"], p["spill_date"], p["spill_est_volume_bbl"]) for p in q7["pins"]])

    q8 = field.spill_heatmap(2026)
    totals: dict[str, int] = {}
    for cell in q8["cells"]:
        totals[cell["area_name"]] = totals.get(cell["area_name"], 0) + cell["spills"]
    print("Q8", totals)


if __name__ == "__main__":
    main()
