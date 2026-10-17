"""Pull operating rules and report facts out of the manuals and repair reports.

The numbers that decide answers (temperature thresholds, spill deadlines,
containment hours) live in documents, not in the record files. Extraction
runs at ingest so a new well-type manual is picked up without a code change.
"""

from __future__ import annotations

import re
from pathlib import Path


_DELTA = re.compile(
    r"at least \*\*(\d+(?:\.\d+)?) °C above",
    re.IGNORECASE,
)
_ALARM = re.compile(
    r"at or below\s*\**\s*(\d+(?:\.\d+)?)\s*°C",
    re.IGNORECASE,
)
_HOURS = re.compile(r"contained about ([0-9]+(?:\.[0-9]+)?) hours", re.IGNORECASE)
_INSPECTION = re.compile(r"\b(INS-\d+-\d+)\b")


def extract_rules(fleet_dir: Path) -> list[dict]:
    rules: list[dict] = []
    for path in sorted((fleet_dir / "well-types").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        well_type = path.stem
        delta = _DELTA.search(text)
        if delta:
            rules.append(_rule(
                f"{well_type}-producing-delta",
                path.name,
                "well_type",
                well_type,
                "producing_delta_c",
                float(delta.group(1)),
                "°C",
                f"{path.name} production temperature rule",
            ))
        alarm = _ALARM.search(text)
        if alarm:
            rules.append(_rule(
                f"{well_type}-temp-alarm",
                path.name,
                "well_type",
                well_type,
                "temp_alarm_c",
                float(alarm.group(1)),
                "°C",
                f"{path.name} temperature alarm",
            ))
        if "three or more" in text.lower() and "root-cause" in text.lower():
            rules.append(_rule(
                f"{well_type}-rca-count",
                path.name,
                "well_type",
                well_type,
                "rca_corrective_count",
                3,
                "events",
                f"{path.name} section 10",
            ))

    spill = (fleet_dir / "sops" / "spill-response-sop.md").read_text(encoding="utf-8")
    rules.extend([
        _rule("SR-01-volume", "spill-response-sop.md", "sop", "SR-01", "reportable_bbl", 5, "bbl", "SOP-SR spill classes"),
        _rule("SR-01-hours", "spill-response-sop.md", "sop", "SR-01", "report_hours", 24, "hours", "SOP-SR SR-01"),
        _rule("SR-02-hours", "spill-response-sop.md", "sop", "SR-02", "contain_hours", 4, "hours", "SOP-SR SR-02"),
        _rule("SR-03-days", "spill-response-sop.md", "sop", "SR-03", "verify_days", 60, "days", "SOP-SR SR-03"),
    ])
    if "24 hours" not in spill or "4 hours" not in spill:
        raise ValueError("Spill SOP no longer contains the expected deadlines; update the extractor.")
    return rules


def extract_report_facts(assets_dir: Path) -> list[dict]:
    facts: list[dict] = []
    for path in assets_dir.glob("*/maintenance-reports/*.md"):
        text = path.read_text(encoding="utf-8")
        hours = _HOURS.search(text)
        inspection = _INSPECTION.search(text)
        if not hours and not inspection:
            continue
        asset_id = path.parents[1].name
        facts.append({
            "maintenance_id": path.stem,
            "asset_id": asset_id,
            "inspection_id": inspection.group(1) if inspection else None,
            "containment_hours": float(hours.group(1)) if hours else None,
            "report_file": f"maintenance-reports/{path.name}",
        })
    return facts


def _rule(rule_id, source, scope_type, scope_value, parameter, value, unit, citation) -> dict:
    return {
        "rule_id": rule_id,
        "source_doc": source,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "parameter": parameter,
        "numeric_value": value,
        "unit": unit,
        "citation": citation,
    }
