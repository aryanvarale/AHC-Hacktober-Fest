"""Evidence lookup: turn an id cited in an answer into the source record, with the parts that were used.

Ids look like INS-0001-0203 (inspection), CTX-0001-003 (note), MNT-0001-029 (repair),
REG-0001-001 (regulator event) or WELL-0001 (well record). Each maps to a line in a
file under sample-data/assets/<well>/, so we can show the original document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

KINDS = {
    "INS": ("inspections.jsonl", "inspection_id", "Inspection"),
    "CTX": ("context.jsonl", "note_id", "Note"),
    "MNT": ("maintenance.jsonl", "maintenance_id", "Repair record"),
    "REG": ("regulatory-events.jsonl", "event_id", "Regulator event"),
}

TEMPERATURE_TOOLS = {"well_status", "non_producing", "timeseries", "downtime_days", "top_downtime"}
SPILL_TOOLS = {"spills_for_well", "spill_map", "spill_heatmap"}


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                rows.append({})
    return rows


def _user(store: Any, user_id: str | None) -> str | None:
    if not user_id:
        return None
    rows = store.query("SELECT name FROM users WHERE user_id = ?", [user_id])
    return rows[0]["name"] if rows else user_id


def _inspection_story(store: Any, eid: str, tool: str) -> tuple[list[str], str, list[dict]]:
    rows = store.query("SELECT * FROM inspection_status WHERE inspection_id = ?", [eid])
    if not rows:
        return [], "", []
    r = rows[0]
    fixes: list[dict] = []
    if r.get("temp_corrected_by"):
        fixes.append({
            "field": "wellhead_temp_c", "original": r["temp_original_value"], "corrected": r["wellhead_temp_c"],
            "by": _user(store, r["temp_corrected_by"]), "reason": r["temp_correction_reason"],
        })
    if r.get("spill_corrected_by"):
        fixes.append({
            "field": "spill_detected", "original": r["spill_detected_original"], "corrected": r["spill_detected"],
            "by": _user(store, r["spill_corrected_by"]), "reason": r["spill_correction_reason"],
        })
    if r.get("volume_corrected_by"):
        fixes.append({
            "field": "spill_est_volume_bbl", "original": r["volume_original_value"], "corrected": r["spill_est_volume_bbl"],
            "by": _user(store, r["volume_corrected_by"]), "reason": r["volume_correction_reason"],
        })

    if tool in SPILL_TOOLS:
        fields = ["inspected_at", "spill_detected", "spill_est_volume_bbl", "spill_area_pct", "notes"]
        if r["spill_detected"] and r["spill_est_volume_bbl"] is not None:
            how = f"This inspection found a spill of {r['spill_est_volume_bbl']} bbl covering {r['spill_area_pct']}% of the pad, so it is counted as a spill."
            if r["spill_est_volume_bbl"] >= 5:
                how += " At 5 bbl or more it must be reported to the regulator within 24 hours."
        else:
            how = "This inspection did not find a spill, so it is not counted."
    else:
        fields = ["inspected_at", "ambient_temp_c", "wellhead_temp_c"]
        verdict = "producing" if r["producing"] else "not producing"
        how = (
            f"Wellhead {r['wellhead_temp_c']:.1f} °C minus air {r['ambient_temp_c']:.1f} °C is {r['delta_c']:.1f} °C above air. "
            f"The {r['well_type']} manual needs at least {r['producing_delta_c']:g} °C, so the well was {verdict}."
        )
        if tool in {"downtime_days", "top_downtime"} and not r["producing"]:
            how += " Each not-producing weekly inspection counts as 7 days of downtime."
    if fixes:
        how += " A correction replaced the original value; the corrected value is the one used."
    return fields, how, fixes


def _rule_evidence(rule_id: str, data_dir: Path, store: Any) -> dict:
    """A cited rule: show the manual or SOP it came from, with the sentence that holds the number marked."""
    rows = store.query("SELECT * FROM rules WHERE lower(rule_id) = lower(?)", [rule_id])
    if not rows:
        return {"error": f"No rule {rule_id} is loaded."}
    rule = rows[0]
    folder = "well-types" if rule["scope_type"] == "well_type" else "sops"
    path = data_dir / "fleet" / folder / rule["source_doc"]
    value, unit = rule["numeric_value"], rule["unit"] or ""
    shown = f"{value:g}"
    out: dict[str, Any] = {
        "id": f"RULE-{rule['rule_id']}", "asset_id": rule["scope_value"], "kind": "RULE", "label": "Rule from the manual",
        "file": f"fleet/{folder}/{rule['source_doc']}", "line": None,
        "record": {"rule_id": rule["rule_id"], "applies_to": rule["scope_value"], "parameter": rule["parameter"],
                   "value": value, "unit": unit, "citation": rule["citation"]},
        "highlight": ["parameter", "value"], "related": [], "fixes": [], "report": None,
        "marks": [f"{shown} {unit}".strip(), f"{shown}{unit}"] if unit else [shown],
    }
    effective = next((r for r in store.query(
        "SELECT * FROM rule_overrides WHERE scope_type = ? AND scope_value = ? AND parameter = ? ORDER BY created_at DESC, override_id DESC LIMIT 1",
        [rule["scope_type"], rule["scope_value"], rule["parameter"]])), None)
    how = f"{rule['citation']}: {rule['parameter'].replace('_', ' ')} for {rule['scope_value']} is {shown} {unit}.".replace("  ", " ")
    if effective is not None:
        out["fixes"] = [{"field": rule["parameter"], "original": value, "corrected": effective["numeric_value"],
                         "by": _user(store, effective["author_user_id"]), "reason": effective["reason"]}]
        how += " A correction replaced this value, and the corrected value is the one used in answers."
    out["how"] = how
    if path.exists():
        out["report"] = {"file": out["file"], "text": path.read_text(encoding="utf-8")}
    return out


def lookup(eid: str, data_dir: Path, store: Any, tool: str = "", focus: str = "") -> dict:
    if eid.strip().upper().startswith("RULE-"):
        return _rule_evidence(eid.strip()[5:], data_dir, store)
    eid = eid.strip().upper()
    well = re.fullmatch(r"WELL-\d{4}", eid)
    m = re.fullmatch(r"(INS|CTX|MNT|REG)-(\d{4})-[\w-]+", eid)
    if not well and not m:
        return {"error": f"{eid} is not an evidence id I can open."}

    asset = eid if well else f"WELL-{m.group(2)}"
    folder = data_dir / "assets" / asset
    if not folder.exists():
        return {"error": f"No records for {asset}."}

    out: dict[str, Any] = {"id": eid, "asset_id": asset, "related": [], "fixes": [], "marks": [], "report": None}

    if well:
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        out.update(kind="WELL", label="Well record", file=f"assets/{asset}/metadata.json", line=None, record=meta,
                   highlight=["well_type", "area_id", "name"],
                   how=f"{asset} is a {meta.get('well_type')} well. Its type sets the temperature rule used to decide whether it is producing, and its weekly inspections give the downtime.")
        return out

    prefix = m.group(1)
    filename, key, label = KINDS[prefix]
    rows = _read_lines(folder / filename)
    idx = next((i for i, r in enumerate(rows) if r.get(key) == eid), None)
    if idx is None:
        return {"error": f"{eid} was not found in {filename}."}
    rec = rows[idx]
    out.update(kind=prefix, label=label, file=f"assets/{asset}/{filename}", line=idx + 1, record=rec)

    if prefix == "INS":
        fields, how, fixes = _inspection_story(store, eid, tool)
        out.update(highlight=fields, how=how, fixes=fixes)
    elif prefix == "CTX":
        who = _user(store, rec.get("author_user_id"))
        out.update(highlight=["text", "note_type", "valid_from", "valid_to"],
                   how=f"{str(rec.get('note_type', 'note')).replace('_', ' ').capitalize()} written by {who} on {str(rec.get('created_at'))[:10]}. Notes give context the raw readings cannot, such as planned shutdowns and corrections.")
    elif prefix == "MNT":
        marks = [x for x in (focus, rec.get("triggered_by_inspection_id")) if x]
        if rec.get("downtime_hours"):
            marks.append(f"{rec['downtime_hours']} hours")
        out.update(highlight=["issue", "action_taken", "downtime_hours", "cost_usd"], marks=marks,
                   how=f"Repair on {str(rec.get('started_at'))[:10]}: {rec.get('issue')}. Repair history explains why a well was stopped.")
        rf = rec.get("report_file")
        if rf:
            p = folder / rf
            if p.exists():
                out["report"] = {"file": f"assets/{asset}/{rf}", "text": p.read_text(encoding="utf-8")}
    else:  # REG
        out.update(highlight=["event_type", "detail", "hours_to_report", "fine_usd", "related_inspection_id"],
                   how=f"{str(rec.get('event_type')).replace('_', ' ').capitalize()} on {rec.get('date')} ({rec.get('agency')}). Regulator events show whether a spill was reported in time.")

    # cross-links: other records of this well that point at this one
    links = []
    for pfx in ("CTX", "MNT", "REG"):
        fn, k, lab = KINDS[pfx]
        for r in _read_lines(folder / fn):
            refs = (r.get("inspection_id"), r.get("triggered_by_inspection_id"), r.get("related_inspection_id"))
            if eid in refs and r.get(k) != eid:
                links.append({"id": r[k], "label": lab, "summary": str(r.get("text") or r.get("issue") or r.get("detail") or "")[:90]})
    if prefix in ("MNT", "REG"):
        ref = rec.get("triggered_by_inspection_id") or rec.get("related_inspection_id")
        if ref:
            links.insert(0, {"id": ref, "label": "Inspection", "summary": "The inspection that led to this record"})
    out["related"] = links[:8]
    return out
