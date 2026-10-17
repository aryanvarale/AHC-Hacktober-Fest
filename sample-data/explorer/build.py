"""Build the searchable catalog consumed by explorer/index.html.

Reads the sample-data tree and writes:
  explorer/data/catalog.json
  explorer/data/wells/WELL-XXXX.json
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "data"
WELLS_OUT = OUT / "wells"

ROLE_LABELS = {
    "production_supervisor": "Production supervisor",
    "field_operator": "Field operator",
    "field_inspector": "Field inspector",
    "maintenance_lead": "Maintenance lead",
    "maintenance_technician": "Maintenance technician",
    "hse_officer": "HSE officer",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def md_to_html(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")), 4)
            out.append(f"<h{level}>{inline(line[level:].strip())}</h{level}>")
            i += 1
            continue
        if line.startswith("> "):
            chunks = []
            while i < len(lines) and lines[i].startswith(">"):
                chunks.append(lines[i].lstrip(">").strip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(chunks))}</p></blockquote>")
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            body = [r for r in rows if not all(set(c) <= set("-: ") and c for c in r)]
            if not body:
                continue
            head, *rest = body
            thead = "".join(f"<th>{inline(c)}</th>" for c in head)
            tbody = []
            for row in rest:
                tbody.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
            out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(tbody)}</tbody></table>")
            continue
        if line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:].strip())}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if re.match(r"\d+\.\s", line):
            items = []
            while i < len(lines) and re.match(r"\d+\.\s", lines[i]):
                items.append(f"<li>{inline(re.sub(r'^\d+\.\s+', '', lines[i]))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        if line.strip() == "---":
            out.append("<hr>")
            i += 1
            continue
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not _block_start(lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)


def _block_start(line: str) -> bool:
    return (
        line.startswith("#")
        or line.startswith(">")
        or line.startswith("|")
        or line.startswith("- ")
        or line.strip() == "---"
        or bool(re.match(r"\d+\.\s", line))
    )


def thresholds(well_type: str) -> tuple[float, float]:
    if well_type == "T350":
        return 20.0, 95.0
    return 15.0, 85.0


def spill_class(volume) -> str | None:
    if volume is None:
        return None
    if volume < 1:
        return "minor"
    if volume < 5:
        return "moderate"
    if volume < 25:
        return "reportable"
    return "major"


def date_only(value: str | None) -> str | None:
    if not value:
        return None
    return value[:10]


ABOUT = """
# How this dataset is put together

This catalog is a readable view of the fictional FlytBase Asset Copilot sample data: 200 onshore rod-lift oil wells, their inspection history, maintenance, operator notes, and regulatory events. It is not a real field and the manuals are not operational guidance.

## Two layers

The `fleet` folder is shared reference. It names the operating areas, the people, the two well classes, and the company procedures.

Each `assets/WELL-xxxx` folder is one well. The same files appear in every folder.

| File | What it records |
|---|---|
| metadata.json | Identity, location, equipment, recorded status, last inspection |
| inspections.jsonl | Drone and manual visits: temperatures and spills |
| context.jsonl | Human notes that explain or correct the readings |
| maintenance.jsonl | Scheduled and corrective jobs, cost, and downtime |
| maintenance-reports | Written report for each corrective job |
| regulatory-events.jsonl | Spill reports, agency inspections, violations, fines |

## How a record links to another

An inspection can trigger a maintenance job (`triggered_by_inspection_id`). That job can point at a markdown report. A regulatory event points back at the inspection that discovered the spill and at a protocol id such as SR-01. A context note can name an inspection and cancel a false spill.

## How to read a temperature

Do not treat the wellhead temperature on its own as proof the well is producing. Compare it with the air temperature on the same reading.

| Well class | Producing when | Alarm when wellhead is above |
|---|---|---|
| T200 | at least 15 °C warmer than the air | 85 °C |
| T350 | at least 20 °C warmer than the air | 95 °C |

`recorded_status` on the well is the status stored on the asset. A single inspection can still say the well was not producing that day.

## Spill classes

| Class | Estimated volume |
|---|---|
| Minor | below 1 barrel |
| Moderate | 1 barrel up to, but not including, 5 |
| Reportable | 5 barrels up to, but not including, 25 |
| Major | 25 barrels or more |

A context note of type correction means a qualified person later decided the dark patch was not crude. Those detections are flagged in the inspection table.
"""


def main() -> None:
    users_doc = load_json(ROOT / "fleet" / "users.json")
    areas_doc = load_json(ROOT / "fleet" / "areas.json")
    users = {u["user_id"]: u for u in users_doc["users"]}
    teams = {t["team_id"]: t for t in areas_doc["teams"]}
    areas = {a["area_id"]: a for a in areas_doc["areas"]}

    def user_name(user_id: str | None) -> str | None:
        if not user_id:
            return None
        user = users.get(user_id)
        return user["name"] if user else user_id

    docs = [
        {
            "id": "about",
            "title": "How this data fits together",
            "kind": "Guide",
            "search_text": ABOUT,
            "html": md_to_html(ABOUT),
        }
    ]
    manual_files = [
        ("t200", "T200 operating manual", "Well type", ROOT / "fleet" / "well-types" / "T200.md"),
        ("t350", "T350 operating manual", "Well type", ROOT / "fleet" / "well-types" / "T350.md"),
        ("sop-ws", "Field work SOP", "Procedure", ROOT / "fleet" / "sops" / "field-work-sop.md"),
        ("sop-sr", "Spill response SOP", "Procedure", ROOT / "fleet" / "sops" / "spill-response-sop.md"),
        ("sop-rc", "Regulatory compliance SOP", "Procedure", ROOT / "fleet" / "sops" / "regulatory-compliance-sop.md"),
    ]
    for doc_id, title, kind, path in manual_files:
        text = path.read_text(encoding="utf-8")
        docs.append({"id": doc_id, "title": title, "kind": kind, "search_text": text, "html": md_to_html(text)})

    activity = defaultdict(lambda: {"inspections": 0, "maintenance": 0, "notes": 0})
    well_summaries = []
    maintenance_index = []
    context_index = []
    regulatory_index = []
    totals = defaultdict(int)
    first_insp = None
    last_insp = None

    WELLS_OUT.mkdir(parents=True, exist_ok=True)
    well_dirs = sorted(p for p in (ROOT / "assets").iterdir() if p.is_dir() and p.name.startswith("WELL-"))

    for folder in well_dirs:
        meta = load_json(folder / "metadata.json")
        well_type = meta["well_type"]
        gap, alarm_at = thresholds(well_type)
        inspections = load_jsonl(folder / "inspections.jsonl")
        maintenance = load_jsonl(folder / "maintenance.jsonl")
        context = load_jsonl(folder / "context.jsonl")
        regulatory = load_jsonl(folder / "regulatory-events.jsonl")

        corrected_ids = {
            note.get("inspection_id")
            for note in context
            if note.get("note_type") == "correction" and note.get("inspection_id")
        }

        report_html = {}
        report_dir = folder / "maintenance-reports"
        if report_dir.exists():
            for report_path in report_dir.glob("*.md"):
                report_html[f"maintenance-reports/{report_path.name}"] = md_to_html(
                    report_path.read_text(encoding="utf-8")
                )

        spill_count = 0
        max_spill = None
        last_spill_at = None
        packed_inspections = []
        for row in inspections:
            delta = None
            if row.get("wellhead_temp_c") is not None and row.get("ambient_temp_c") is not None:
                delta = round(row["wellhead_temp_c"] - row["ambient_temp_c"], 1)
            producing = delta is not None and delta >= gap
            alarm = row.get("wellhead_temp_c") is not None and row["wellhead_temp_c"] > alarm_at
            volume = row.get("spill_est_volume_bbl")
            if row.get("spill_detected"):
                spill_count += 1
                if volume is not None:
                    max_spill = volume if max_spill is None else max(max_spill, volume)
                last_spill_at = row["inspected_at"]
            inspector_id = row.get("inspector_user_id")
            if inspector_id:
                activity[inspector_id]["inspections"] += 1
            at = row["inspected_at"]
            if first_insp is None or at < first_insp:
                first_insp = at
            if last_insp is None or at > last_insp:
                last_insp = at
            packed_inspections.append(
                {
                    "id": row["inspection_id"],
                    "at": at,
                    "inspector_id": inspector_id,
                    "inspector": user_name(inspector_id),
                    "method": row.get("method"),
                    "ambient_c": row.get("ambient_temp_c"),
                    "wellhead_c": row.get("wellhead_temp_c"),
                    "delta_c": delta,
                    "producing": producing,
                    "alarm": alarm,
                    "spill": bool(row.get("spill_detected")),
                    "spill_area_pct": row.get("spill_area_pct"),
                    "spill_bbl": volume,
                    "spill_class": spill_class(volume) if row.get("spill_detected") else None,
                    "corrected": row["inspection_id"] in corrected_ids,
                    "notes": row.get("notes") or "",
                    "evidence": row.get("evidence_images") or [],
                }
            )

        packed_maintenance = []
        corrective = 0
        for row in maintenance:
            if row.get("maintenance_type") == "corrective":
                corrective += 1
            tech_id = row.get("technician_user_id")
            if tech_id:
                activity[tech_id]["maintenance"] += 1
            report_key = row.get("report_file")
            packed_maintenance.append(
                {
                    "id": row["maintenance_id"],
                    "started_at": row.get("started_at"),
                    "completed_at": row.get("completed_at"),
                    "type": row.get("maintenance_type"),
                    "equipment": row.get("equipment_kind"),
                    "issue": row.get("issue"),
                    "action": row.get("action_taken"),
                    "technician_id": tech_id,
                    "technician": user_name(tech_id),
                    "contractor": row.get("contractor"),
                    "downtime_hours": row.get("downtime_hours"),
                    "cost": (row.get("cost_usd") or {}).get("total"),
                    "labor": (row.get("cost_usd") or {}).get("labor"),
                    "parts": (row.get("cost_usd") or {}).get("parts"),
                    "triggered_by": row.get("triggered_by_inspection_id"),
                    "report_html": report_html.get(report_key),
                }
            )
            maintenance_index.append(
                {
                    "id": row["maintenance_id"],
                    "asset_id": meta["asset_id"],
                    "at": date_only(row.get("started_at")),
                    "type": row.get("maintenance_type"),
                    "equipment": row.get("equipment_kind"),
                    "issue": row.get("issue") or "",
                    "action": row.get("action_taken") or "",
                    "technician": user_name(tech_id) or "",
                    "cost": (row.get("cost_usd") or {}).get("total"),
                }
            )

        packed_context = []
        for row in context:
            author_id = row.get("author_user_id")
            if author_id:
                activity[author_id]["notes"] += 1
            packed_context.append(
                {
                    "id": row.get("note_id"),
                    "at": row.get("created_at"),
                    "type": row.get("note_type"),
                    "author_id": author_id,
                    "author": user_name(author_id),
                    "text": row.get("text") or "",
                    "inspection_id": row.get("inspection_id"),
                    "valid_from": row.get("valid_from"),
                    "valid_to": row.get("valid_to"),
                }
            )
            context_index.append(
                {
                    "id": row.get("note_id"),
                    "asset_id": meta["asset_id"],
                    "at": date_only(row.get("created_at")),
                    "type": row.get("note_type"),
                    "who": user_name(author_id) or "",
                    "text": row.get("text") or "",
                }
            )

        packed_regulatory = []
        open_regs = 0
        for row in regulatory:
            if row.get("status") == "open":
                open_regs += 1
            packed_regulatory.append(
                {
                    "id": row.get("event_id"),
                    "date": row.get("date"),
                    "agency": row.get("agency"),
                    "type": row.get("event_type"),
                    "protocol": row.get("protocol_id"),
                    "inspection_id": row.get("related_inspection_id"),
                    "detail": row.get("detail") or "",
                    "hours_to_report": row.get("hours_to_report"),
                    "fine_usd": row.get("fine_usd"),
                    "status": row.get("status"),
                }
            )
            regulatory_index.append(
                {
                    "id": row.get("event_id"),
                    "asset_id": meta["asset_id"],
                    "date": row.get("date"),
                    "type": row.get("event_type"),
                    "status": row.get("status"),
                    "protocol": row.get("protocol_id"),
                    "detail": row.get("detail") or "",
                    "fine_usd": row.get("fine_usd") or 0,
                }
            )

        linked = {row.get("report_file") for row in maintenance if row.get("report_file")}
        unlinked_reports = [
            {"file": key, "html": value}
            for key, value in report_html.items()
            if key not in linked
        ]

        last = meta.get("last_inspection") or {}
        latest = packed_inspections[-1] if packed_inspections else None
        area = areas.get(meta["area_id"], {})
        equipment = [
            {"id": item["equipment_id"], "kind": item["kind"], "model": item["model"]}
            for item in meta.get("equipment", [])
        ]
        summary = {
            "asset_id": meta["asset_id"],
            "name": meta["name"],
            "well_type": well_type,
            "area_id": meta["area_id"],
            "area_name": area.get("name"),
            "team_id": meta["team_id"],
            "in_charge_id": meta.get("in_charge_user_id"),
            "in_charge": user_name(meta.get("in_charge_user_id")),
            "lat": meta["location"]["lat"],
            "lon": meta["location"]["lon"],
            "installed_on": meta.get("installed_on"),
            "status": meta.get("recorded_status"),
            "equipment": equipment,
            "has_separator": any(item["kind"] == "separator" for item in equipment),
            "producing_gap_c": gap,
            "alarm_c": alarm_at,
            "inspection_count": len(packed_inspections),
            "spill_count": spill_count,
            "max_spill_bbl": max_spill,
            "last_spill_at": last_spill_at,
            "maintenance_count": len(packed_maintenance),
            "corrective_count": corrective,
            "context_count": len(packed_context),
            "regulatory_count": len(packed_regulatory),
            "open_regulatory": open_regs,
            "report_count": sum(1 for row in packed_maintenance if row["report_html"]),
            "last_inspection_id": last.get("inspection_id"),
            "last_inspection_at": last.get("inspected_at"),
            "last_inspector": user_name(last.get("inspector_user_id")),
            "latest_delta_c": latest["delta_c"] if latest else None,
            "latest_producing": latest["producing"] if latest else None,
        }
        well_summaries.append(summary)
        totals["inspections"] += len(packed_inspections)
        totals["spills"] += spill_count
        totals["maintenance"] += len(packed_maintenance)
        totals["corrective"] += corrective
        totals["context"] += len(packed_context)
        totals["regulatory"] += len(packed_regulatory)
        totals["open_regulatory"] += open_regs
        totals["reports"] += summary["report_count"]
        if well_type == "T350":
            totals["t350"] += 1
        else:
            totals["t200"] += 1
        if summary["status"] == "down":
            totals["down"] += 1
        else:
            totals["active"] += 1

        payload = {
            "asset_id": meta["asset_id"],
            "inspections": packed_inspections,
            "maintenance": packed_maintenance,
            "context": packed_context,
            "regulatory": packed_regulatory,
            "unlinked_reports": unlinked_reports,
        }
        (WELLS_OUT / f"{meta['asset_id']}.json").write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )

    catalog_users = []
    for user in users_doc["users"]:
        stats = activity.get(user["user_id"], {"inspections": 0, "maintenance": 0, "notes": 0})
        catalog_users.append(
            {
                "id": user["user_id"],
                "name": user["name"],
                "role": user["role"],
                "role_label": ROLE_LABELS.get(user["role"], user["role"]),
                "team_id": user["team_id"],
                "team_name": teams.get(user["team_id"], {}).get("name"),
                "inspections": stats["inspections"],
                "maintenance": stats["maintenance"],
                "notes": stats["notes"],
            }
        )

    catalog_teams = []
    for team in areas_doc["teams"]:
        catalog_teams.append(
            {
                "id": team["team_id"],
                "name": team["name"],
                "lead_id": team["lead_user_id"],
                "lead": user_name(team["lead_user_id"]),
            }
        )

    catalog_areas = []
    for area in areas_doc["areas"]:
        catalog_areas.append(
            {
                "id": area["area_id"],
                "name": area["name"],
                "team_id": area["team_id"],
                "team_name": teams.get(area["team_id"], {}).get("name"),
                "lat": area["center"]["lat"],
                "lon": area["center"]["lon"],
                "well_count": len(area["asset_ids"]),
            }
        )

    catalog = {
        "stats": {
            "wells": len(well_summaries),
            "t200": totals["t200"],
            "t350": totals["t350"],
            "active": totals["active"],
            "down": totals["down"],
            "inspections": totals["inspections"],
            "spills": totals["spills"],
            "maintenance": totals["maintenance"],
            "corrective": totals["corrective"],
            "context": totals["context"],
            "regulatory": totals["regulatory"],
            "open_regulatory": totals["open_regulatory"],
            "reports": totals["reports"],
            "inspection_from": first_insp,
            "inspection_to": last_insp,
        },
        "teams": catalog_teams,
        "areas": catalog_areas,
        "users": catalog_users,
        "docs": docs,
        "wells": well_summaries,
        "maintenance": maintenance_index,
        "context": context_index,
        "regulatory": regulatory_index,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "catalog.json").write_text(json.dumps(catalog, separators=(",", ":")), encoding="utf-8")
    print(
        f"Wrote {len(well_summaries)} wells, "
        f"{totals['inspections']} inspections, "
        f"{totals['maintenance']} maintenance rows -> {OUT}"
    )


if __name__ == "__main__":
    main()
