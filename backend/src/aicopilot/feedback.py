"""People telling the copilot something, as opposed to asking it something.

Three kinds of statement are recognised and written down, always keeping what was there before:

* a correction to a reading or to the AI's own estimate ("the 15 Oct reading for WELL-0164 is wrong ... 41.0 °C")
* a rule for a whole well type ("for all T350 wells, a root-cause analysis after 2 repairs, not 3")
* a note meant for everybody ("FYI: WELL-0007 will be shut in from 17 Oct for a 10-day workover")

The corrections and rule overrides are read by the same database views every answer uses, so a statement
made once reaches every user and every later answer. Each reply says what was recorded, by whom, what changed,
and what it now means (including anything that disagrees with it).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from aicopilot.queries import FieldQueries

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"

_WELL = re.compile(r"\bWELL-\d{3,4}\b", re.I)
_INSPECTION = re.compile(r"\bINS-\d{4}-\d{4}\b", re.I)
_TYPE = re.compile(r"\bT[-\s]?(\d{3})\b", re.I)
_QUESTION_START = re.compile(r"^\s*(is|are|was|were|what|which|how|show|why|when|who|can|could|does|do|did|list|give|tell)\b", re.I)
_CUES = re.compile(
    r"\b(wrong|incorrect|faulty|mistake|mistaken|misread|re-?measured|measured|actually|real value|should be|in error|"
    r"not a spill|not oil|estimated|over-?estimated|under-?estimated|inaccurate|false reading)\b",
    re.I,
)
_TEMP = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°|º|degrees?|deg)\s*c?", re.I)
_VOLUME = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:bbl|barrels?)", re.I)
_NOT_OIL = re.compile(r"\b(?:not|isn'?t|wasn'?t)\s+(?:a\s+|an\s+)?(?:real\s+)?(?:spill|oil|crude)\b|\b(?:mud|rain ?water|puddle|water)\b[^.]{0,20}\bnot\b", re.I)
_STOPPED_WORDS = re.compile(r"not moving|no stroke|shut|stopped|down|idle|not running|seized|stuck", re.I)
_RUNNING_WORDS = re.compile(r"running|producing|normal|stroking|operating", re.I)


def _n(v: Any) -> str:
    return f"{round(float(v), 2):g}"


def _dates_in(text: str, today: date) -> list[date]:
    found: list[date] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        found.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"(?:\s+(\d{4}))?\b", text, re.I):
        found.append(_make_date(int(m.group(1)), _MONTHS[m.group(2).lower()], m.group(3), today))
    for m in re.finditer(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", text, re.I):
        found.append(_make_date(int(m.group(2)), _MONTHS[m.group(1).lower()], m.group(3), today))
    return [d for d in found if d]


def _make_date(day: int, month: int, year: str | None, today: date) -> date | None:
    try:
        y = int(year) if year else today.year
        d = date(y, month, day)
        return d if year or d <= today + timedelta(days=200) else date(y - 1, month, day)
    except ValueError:
        return None


def _user_name(store: Any, user_id: str) -> str:
    rows = store.query("SELECT name FROM users WHERE user_id = ?", [user_id])
    return rows[0]["name"] if rows else user_id


def _payload(tool: str, arguments: dict, result: dict, answer: str, visual: str = "table") -> dict:
    return {
        "visual": visual, "title": tool, "tool": tool, "arguments": arguments, "result": result, "answer": answer,
        "tools_used": [{"tool": tool, "arguments": arguments}], "model": False,
    }


def handle(field: FieldQueries, store: Any, question: str, user_id: str, today: date, data_dir: Path) -> dict | None:
    """A finished reply if the message is a statement to record, else None (it is a question)."""
    text = " ".join(question.split())
    if not text or text.endswith("?") or _QUESTION_START.match(text):
        return None
    for handler in (_rule_statement, _shared_note, _correction):
        reply = handler(field, store, text, user_id, today, data_dir)
        if reply is not None:
            return reply
    return None


# ───────────────────────────── a correction to a reading or an estimate ─────────────────────────────


def _correction(field: FieldQueries, store: Any, text: str, user_id: str, today: date, data_dir: Path) -> dict | None:
    wells = list(dict.fromkeys(w.upper() for w in _WELL.findall(text)))
    if len(wells) != 1 or not _CUES.search(text):
        return None
    asset = wells[0]

    volume, temps = _VOLUME.findall(text), _TEMP.findall(text)
    if volume and re.search(r"\b(spill|leak|crude|oil)\b", text, re.I):
        column, new_value, label = "spill_est_volume_bbl", volume[-1], "spill volume"
    elif _NOT_OIL.search(text):
        column, new_value, label = "spill_detected", "false", "spill flag"
    elif temps:
        air_only = re.search(r"\b(air|ambient)\b", text, re.I) and not re.search(r"well\s*-?\s*head", text, re.I)
        column, new_value, label = ("ambient_temp_c", temps[-1], "air temperature") if air_only else ("wellhead_temp_c", temps[-1], "wellhead temperature")
    else:
        return None

    explicit = _INSPECTION.search(text)
    rows: list[dict] = []
    if explicit:
        rows = store.query("SELECT * FROM inspections WHERE inspection_id = ?", [explicit.group(0).upper()])
    else:
        days = _dates_in(text, today)
        if days:
            rows = store.query("SELECT * FROM inspections WHERE asset_id = ? AND inspected_at::DATE = ?", [asset, days[0].isoformat()])
        elif re.search(r"\b(latest|last|most recent|current|today'?s?)\b", text, re.I):
            rows = store.query("SELECT * FROM inspections WHERE asset_id = ? ORDER BY inspected_at DESC LIMIT 1", [asset])
        else:
            return _ask(f"Which inspection of {asset} do you mean? Give me its date (for example 15 Oct) or its INS id and I'll record the correction.")
    if not rows:
        recent = store.query("SELECT inspected_at::DATE AS d FROM inspections WHERE asset_id = ? ORDER BY inspected_at DESC LIMIT 4", [asset])
        known = ", ".join(str(r["d"]) for r in recent) or "none"
        return _ask(f"I can't find that inspection of {asset}. Its most recent inspections are on {known}. Which one do you mean?")

    insp = rows[0]
    if insp["asset_id"] != asset:
        return _ask(f"{insp['inspection_id']} belongs to {insp['asset_id']}, not {asset}. Which well and inspection do you mean?")
    recorded = field.record_correction(
        asset_id=asset, inspection_id=insp["inspection_id"], field=column, new_value=new_value, author_user_id=user_id, reason=text[:400],
    )
    if recorded.get("error"):
        return _ask(recorded["error"])

    author = _user_name(store, user_id)
    after = store.query(
        "SELECT s.*, u.name AS inspector_name FROM inspection_status s LEFT JOIN users u ON u.user_id = s.inspector_user_id WHERE s.inspection_id = ?",
        [insp["inspection_id"]],
    )[0]
    when = str(insp["inspected_at"])
    original = recorded["original"]
    unit = {"wellhead_temp_c": " °C", "ambient_temp_c": " °C", "spill_est_volume_bbl": " bbl"}.get(column, "")
    shown_new = new_value if column == "spill_detected" else _n(new_value)
    shown_old = original if column == "spill_detected" else _n(original)

    parts = [
        f"Recorded. I corrected the {label} on {insp['inspection_id']} ({asset}, {after['well_name']}, {when[:10]} at {when[11:16]}) "
        f"from {shown_old}{unit} to {shown_new}{unit}, attributed to {author} with the reason you gave. "
        "The original value is kept on file with who changed it and why, and every later answer for every user now uses the corrected value."
    ]
    result: dict[str, Any] = {
        "recorded": True, "inspection_id": insp["inspection_id"], "asset_id": asset, "field": column,
        "changes": [{"what": label, "original": original, "corrected": new_value, "corrected_by": author}],
        "evidence": [insp["inspection_id"]],
    }

    if column in ("wellhead_temp_c", "ambient_temp_c"):
        verdict = "producing" if after["producing"] else "not producing"
        parts.append(
            f"With {_n(after['wellhead_temp_c'])} °C at the wellhead against {_n(after['ambient_temp_c'])} °C air, the well is {_n(after['delta_c'])} °C warmer, "
            f"{'above' if after['producing'] else 'below'} the {after['well_type']} limit of {_n(after['producing_delta_c'])} °C, so {asset} is {verdict} on that reading."
        )
        note = (after["notes"] or "").strip()
        if note and ((after["producing"] and _STOPPED_WORDS.search(note)) or (not after["producing"] and _RUNNING_WORDS.search(note))):
            parts.append(
                f"The same inspection has the note \"{note}\" ({after['inspector_name']}), which disagrees with the corrected reading. "
                f"{author}, can you confirm the corrected value?"
            )
            result["conflict"] = {"note": note, "by": after["inspector_name"]}
        result["status_after"] = {"producing": bool(after["producing"]), "gap_c": round(after["delta_c"], 2), "required_gap_c": after["producing_delta_c"]}
    elif column == "spill_est_volume_bbl":
        parts.extend(_spill_consequences(field, store, after, float(new_value), data_dir))
        result["evidence"].extend(r["event_id"] for r in store.query("SELECT event_id FROM reg_events WHERE related_inspection_id = ?", [insp["inspection_id"]]))
    else:
        parts.append("That detection no longer counts as a spill in totals, maps or reports." if new_value == "false" else "That detection now counts as a spill.")
    return _payload("record_correction", {"inspection_id": insp["inspection_id"], "field": column, "new_value": new_value}, result, " ".join(parts))


def _spill_consequences(field: FieldQueries, store: Any, row: dict, volume: float, data_dir: Path) -> list[str]:
    """What a changed spill volume means under the spill SOP."""
    report_hours = field._rule_number("report_hours", "SR-01", 24)
    reportable = field._rule_number("reportable_bbl", "SR-01", 5)
    contain_limit = field._rule_number("contain_hours", "SR-02", 4)
    verify_days = field._rule_number("verify_days", "SR-03", 60)
    inspected = row["inspected_at"] if isinstance(row["inspected_at"], datetime) else datetime.fromisoformat(str(row["inspected_at"]))
    out: list[str] = []
    if volume >= reportable:
        events = store.query("SELECT event_type FROM reg_events WHERE related_inspection_id = ?", [row["inspection_id"]])
        reported = any(e["event_type"] == "spill_report_filed" for e in events)
        due = inspected + timedelta(hours=report_hours)
        line = f"At {_n(volume)} bbl the spill is reportable (the SOP threshold is {_n(reportable)} bbl): a written notice was due within {_n(report_hours)} hours of the {inspected:%d %b %H:%M} inspection, by {due:%d %b %H:%M}."
        line += " A spill report is on record." if reported else " No notice is recorded, so this is a possible SR-01 breach."
        out.append(line)
        out.append(f"Because it is now reportable, agency verification of the clean-up is also due within {_n(verify_days)} days, by {(inspected + timedelta(days=verify_days)):%d %b %Y}.")
    else:
        out.append(f"At {_n(volume)} bbl the spill is below the {_n(reportable)} bbl reporting threshold, so no regulator notice is required.")

    for fact in store.query("SELECT maintenance_id, containment_hours, report_file FROM report_facts WHERE inspection_id = ?", [row["inspection_id"]]):
        text = ""
        try:
            text = (data_dir / "assets" / row["asset_id"] / fact["report_file"]).read_text(encoding="utf-8")
        except OSError:
            pass
        classed = re.search(r"\b(minor|moderate|reportable|major)\b[^.\n]{0,30}\bspill\b|\bspill\b[^.\n]{0,40}\b(?:class(?:ed|ified)?|categor\w+)[^.\n]{0,15}\b(minor|moderate|reportable|major)\b", text, re.I)
        note = f"Repair report {fact['maintenance_id']}"
        if classed:
            label = (classed.group(1) or classed.group(2)).lower()
            if volume >= reportable and label != "reportable":
                note += f" classed the spill as {label} on the old estimate; that no longer holds"
            else:
                note += f" classed the spill as {label}"
        if fact["containment_hours"] is not None:
            over = fact["containment_hours"] > contain_limit
            note += f"{'; ' if classed else ' '}shows containment took about {_n(fact['containment_hours'])} hours" + (f", over the {_n(contain_limit)}-hour SR-02 limit" if over else "")
        out.append(note + ".")
    return out


# ───────────────────────────── a rule for a whole well type ─────────────────────────────


def _rule_statement(field: FieldQueries, store: Any, text: str, user_id: str, today: date, data_dir: Path) -> dict | None:
    scope = re.search(r"\b(?:for|note for|rule for|applies to)\s+all\s+(T[-\s]?\d{3})\s+wells?\b|\ball\s+(T[-\s]?\d{3})\s+wells?\b", text, re.I)
    if not scope:
        return None
    well_type = re.sub(r"[-\s]", "", (scope.group(1) or scope.group(2)).upper())
    rca = re.search(r"after\s+(\d+)\s+(?:corrective\s+)?(?:repairs?|events?|jobs?)(?:[^.]{0,30}?\bnot\s+(\d+))?", text, re.I)
    gap = re.search(r"(\d+(?:\.\d+)?)\s*(?:°|º|degrees?)?\s*c?\s*(?:above|over|warmer than)\s+(?:the\s+)?(?:air|ambient)", text, re.I)
    if rca:
        parameter, value, unit = "rca_corrective_count", float(rca.group(1)), "repairs"
        meaning = f"a root-cause analysis after {rca.group(1)} corrective pump jack repairs"
    elif gap and re.search(r"\bproducing\b", text, re.I):
        parameter, value, unit = "producing_delta_c", float(gap.group(1)), "°C"
        meaning = f"a well counts as producing at {gap.group(1)} °C above the air"
    else:
        return None

    known = {r["scope_value"] for r in store.query("SELECT DISTINCT scope_value FROM rules WHERE scope_type = 'well_type'")}
    if well_type not in known:
        return _ask(f"I have no manual for well type {well_type} (known types: {', '.join(sorted(known))}). Which type do you mean?")
    manual = next((r for r in field.effective_rules() if r["scope_value"] == well_type and r["parameter"] == parameter), None)
    store.execute(
        "INSERT INTO rule_overrides VALUES (?, 'well_type', ?, ?, ?, ?, ?, ?)",
        [f"OVR-{uuid.uuid4().hex[:8]}", well_type, parameter, value, user_id, text[:400], datetime.now()],
    )
    author = _user_name(store, user_id)
    was = f" instead of the manual's {_n(manual['manual_value'])}" if manual and manual["manual_value"] is not None else ""
    answer = (
        f"Recorded as {author}'s rule for all {well_type} wells: {meaning}{was}. It applies to {well_type} wells only, "
        f"so other well types keep their own values, and from now on answers for every user follow this rule and say it comes from {author}, not the manual."
    )
    result = {"recorded": True, "well_type": well_type, "parameter": parameter, "value": value, "unit": unit, "by": author,
              "changes": [{"what": f"{parameter} for {well_type}", "original": manual["manual_value"] if manual else None, "corrected": value, "corrected_by": author}],
              "evidence": []}
    return _payload("record_rule", {"well_type": well_type, "parameter": parameter, "value": value}, result, answer)


# ───────────────────────────── a note meant for everybody ─────────────────────────────


def _shared_note(field: FieldQueries, store: Any, text: str, user_id: str, today: date, data_dir: Path) -> dict | None:
    wells = list(dict.fromkeys(w.upper() for w in _WELL.findall(text)))
    if len(wells) != 1:
        return None
    if not (re.search(r"\b(fyi|for everyone|heads up|please note|note:)\b", text, re.I) or re.search(r"\bwill be\b", text, re.I)):
        return None
    if not re.search(r"\b(shut ?in|shutdown|shut down|workover|planned (?:maintenance|work|downtime)|out of service|offline|back online)\b", text, re.I):
        return None
    asset = wells[0]
    if not field.well_exists(asset):
        return _ask(f"{asset} is not in the records. Which well do you mean?")
    days = _dates_in(text, today)
    start = days[0] if days else today
    length = re.search(r"(\d+)[-\s]?day", text, re.I)
    end = start + timedelta(days=int(length.group(1))) if length else (days[1] if len(days) > 1 else None)
    existing = store.query("SELECT count(*) AS n FROM notes WHERE asset_id = ?", [asset])[0]["n"]
    note_id = f"CTX-{asset[5:]}-U{existing + 1:02d}"
    store.execute(
        "INSERT INTO notes VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
        [note_id, asset, datetime.now(), user_id, "planned_shutdown", text[:500], start.isoformat(), end.isoformat() if end else None],
    )
    author = _user_name(store, user_id)
    span = f"from {start:%d %b %Y}" + (f" until {end:%d %b %Y}" if end else "")
    answer = f"Noted for everyone: {asset} is planned to be shut in {span}, recorded as {author}'s note {note_id}. Anyone who asks about {asset} will see it, marked as {author}'s statement rather than a confirmed record."
    result = {"recorded": True, "note_id": note_id, "asset_id": asset, "valid_from": start.isoformat(), "valid_to": end.isoformat() if end else None,
              "changes": [{"what": "planned shutdown", "original": None, "corrected": span, "corrected_by": author}], "evidence": [note_id]}
    return _payload("record_note", {"asset_id": asset}, result, answer)


def _ask(text: str) -> dict:
    return {"visual": "answer", "title": "One more detail", "tool": "feedback", "arguments": {}, "result": {"message": text},
            "answer": text, "tools_used": [{"tool": "feedback", "arguments": {}}], "model": False}
