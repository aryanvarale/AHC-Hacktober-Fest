"""Questions that should never reach a language model's memory.

Two kinds of question have an exact answer that sits in the data, so guessing is the only way to get them wrong:

* a made-up reading ("a T200 inspection shows air at 20 °C and a wellhead at 32 °C: producing?")
* a rule lookup ("what gap does a T350 need?", "when must a spill be reported?")

Both are recognised here from the question's own words and answered from the rules extracted from the
manuals (with user corrections applied). Anything else goes on to the model as before.
"""

from __future__ import annotations

import re
from typing import Any

from datetime import date

from aicopilot import memory
from aicopilot.queries import FieldQueries

_NUM = r"(-?\d+(?:\.\d+)?)"
_DEG = r"(?:\s*(?:°|º|degrees?|deg)\s*c?(?:elsius)?|\s*c(?:elsius)?)\b"

_AIR = [
    re.compile(r"(?:air|ambient|outside|surrounding)(?:\s+temp(?:erature)?)?[^0-9\-]{0,30}?" + _NUM + _DEG, re.I),
    re.compile(_NUM + _DEG + r"\s*(?:of\s+|in\s+)?(?:air|ambient)\b", re.I),
]
_WELLHEAD = [
    re.compile(r"well\s*-?\s*head(?:\s+temp(?:erature)?)?[^0-9\-]{0,30}?" + _NUM + _DEG, re.I),
    re.compile(_NUM + _DEG + r"\s*(?:at|on|of)?\s*(?:the\s+)?well\s*-?\s*head", re.I),
]
_TYPE = re.compile(r"\bT[-\s]?(\d{3})\b", re.I)
_WELL_ID = re.compile(r"\bWELL-?\d+\b", re.I)
_AREA = re.compile(r"\b(northeast|southwest|block\s+\d+)\b", re.I)
_RULE_WORDS = re.compile(
    r"\b(threshold|gap|limit|alarm|rules?|requires?|required|needs?|needed|minimum|how much (?:warmer|hotter)|"
    r"(?:above|over) (?:the )?(?:air|ambient)|deadline|reportable|within how many|how many hours|how long|"
    r"contain(?:ed|ment)?|verif(?:y|ication))\b",
    re.I,
)
_RULE_SUBJECT = re.compile(r"\b(well types?|types? of wells?|manuals?|sops?|procedures?|regulations?|spills?)\b", re.I)
_COUNT_WELLS = re.compile(
    r"\b(?:how many|number of|count of|total(?: number of)?)\s+wells?\b"
    r"|\b(?:how large|size of)\b[^?.]*\b(?:dataset|data set|database|fleet)\b"
    r"|\bwells?\b[^?.]*\b(?:in|across) (?:the |this |our )?(?:dataset|data set|database|fleet|system)\b",
    re.I,
)
_REPAIR_KINDS = re.compile(
    r"\b(types? of repairs?|repair types?|kinds? of repairs?|which repairs?)\b"
    r"|\brepairs?\b[^?.]{0,50}\bequipment\b"
    r"|\bequipment\b[^?.]{0,50}\brepairs?\b",
    re.I,
)
_AGGREGATE = re.compile(
    r"\b(how many|number of|count of|total)\s+(?:\w+\s+){0,2}(?:spills?|wells?|fines?|repairs?|inspections?|events?|incidents?|reports?|notes?|violations?|breaches?)\b"
    r"|\b(?:most|least|highest|lowest|average|top \d+|ranking|rank)\b",
    re.I,
)
_AREA_WORDS = re.compile(r"\b(areas?|blocks?|regions?|locations?)\b", re.I)
_STATUS_WORDS = re.compile(r"\b(producing|running|active|shut ?in|down|not producing|status)\b", re.I)
_SPILL_WORDS = re.compile(r"\b(spills?|reports?|reportable|regulator|contain(?:ed|ment)?|verif(?:y|ication)|deadline)\b", re.I)


def _first(patterns: list[re.Pattern], text: str) -> float | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return float(match.group(1))
    return None


def types_in(question: str) -> list[str]:
    seen: list[str] = []
    for digits in _TYPE.findall(question):
        name = f"T{digits}"
        if name not in seen:
            seen.append(name)
    return seen


def readings(question: str) -> tuple[float, float] | None:
    """(air, wellhead) when the question states both temperatures."""
    air, wellhead = _first(_AIR, question), _first(_WELLHEAD, question)
    return (air, wellhead) if air is not None and wellhead is not None else None


def _n(value: float | None) -> str:
    return "?" if value is None else f"{round(float(value), 2):g}"


def _reading_answer(result: dict) -> str:
    reading, rows = result["reading"], {r["well_type"]: r for r in result["types"]}
    first, asked = result["well_type"], result["asked_types"]
    gap = f"{_n(reading['gap_c'])} °C"
    sums = f"{_n(reading['wellhead_c'])} °C wellhead minus {_n(reading['ambient_c'])} °C air"
    parts: list[str] = []

    if first and first in rows:
        row = rows[first]
        if row["producing_on_reading"]:
            parts.append(f"Yes. On that reading a {first} well is producing: the gap is {gap} ({sums}) and a {first} needs at least {_n(row['required_gap_c'])} °C.")
        else:
            parts.append(f"No. On that reading a {first} well is not producing: the gap is {gap} ({sums}), under the {_n(row['required_gap_c'])} °C a {first} needs, so it is {_n(row['shortfall_c'])} °C short.")
        if row["above_alarm"]:
            parts.append(f"The wellhead is also above the {first} alarm limit of {_n(row['alarm_limit_c'])} °C.")
    elif first:
        parts.append(f"The gap is {gap} ({sums}), but no manual is loaded for {first}.")
    else:
        parts.append(f"The gap is {gap} ({sums}).")

    others = [t for t in asked if t != first] or ([t for t in rows if t != first] if not first else [])
    for name in others:
        row = rows.get(name)
        if row is None:
            parts.append(f"No manual is loaded for {name} (known types: {', '.join(sorted(rows))}).")
            continue
        verdict = "producing" if row["producing_on_reading"] else f"not producing ({_n(row['shortfall_c'])} °C short)"
        parts.append(f"A {name} needs a gap of {_n(row['required_gap_c'])} °C, so this reading would be {verdict}. Its alarm limit is {_n(row['alarm_limit_c'])} °C.")
    parts.append("This is a hypothetical reading, not a real well.")
    return " ".join(parts)


_SOP_PHRASE = {
    "reportable_bbl": lambda v, u: f"a spill of {_n(v)} {u} or more is reportable",
    "report_hours": lambda v, u: f"it must be reported to the regulator within {_n(v)} {u}",
    "contain_hours": lambda v, u: f"it must be contained within {_n(v)} {u}",
    "verify_days": lambda v, u: f"clean-up is verified within {_n(v)} {u}",
}


def _rules_answer(result: dict) -> str:
    rows = {t["well_type"]: t for t in result["types"]}
    asked, topic = result.get("asked_types") or [], result.get("topic")
    lines: list[str] = []
    if topic != "spill":
        for name in asked or sorted(rows):
            row = rows.get(name)
            if row is None:
                lines.append(f"No manual is loaded for {name} (known types: {', '.join(result['known_types'])}).")
                continue
            line = f"{name}: a well is producing only when its wellhead is at least {_n(row.get('producing_delta_c'))} °C above the air"
            if row.get("temp_alarm_c") is not None:
                line += f", and its wellhead alarm limit is {_n(row['temp_alarm_c'])} °C"
            if "producing_delta_c" in row.get("overridden", {}):
                line += f" (a correction replaced the manual's {_n(row['overridden']['producing_delta_c']['manual_value'])} °C)"
            lines.append(line + ".")
    if topic != "temperature" and result["sops"]:
        bits = [_SOP_PHRASE[s["parameter"]](s["value"], s["unit"]) for s in result["sops"] if s["parameter"] in _SOP_PHRASE]
        if bits:
            lines.append("Spill rules: " + "; ".join(bits) + ".")
    return " ".join(lines) or "I could not find a matching rule in the manuals."


def _fleet_answer(res: dict, types: list[str], question: str) -> str:
    by_type = res["by_type"]
    shown = [t for t in types if t in by_type] if types else sorted(by_type)
    missing = [t for t in types if t not in by_type]
    parts = [f"The dataset has {res['total_wells']} wells"]
    if shown:
        parts[0] += ": " + ", ".join(f"{by_type[t]} {t}" for t in shown) + "."
    else:
        parts[0] += "."
    if missing:
        parts.append(f"There are no {', '.join(missing)} wells in it (types present: {', '.join(sorted(by_type))}).")
    if _AREA_WORDS.search(question):
        parts.append("By area: " + "; ".join(f"{n} in {a}" for a, n in res["by_area"].items()) + ".")
    if _STATUS_WORDS.search(question):
        parts.append(f"By the latest inspections, {res['producing_now']} are producing and {res['not_producing_now']} are not.")
    return " ".join(parts)


_COST = re.compile(r"\b(cost|costs|spent|spend|spending|expenses?|paid|how much)\b", re.I)
_REPAIR = re.compile(r"\b(repairs?|maintenance|work|jobs?|cost|costs)\b", re.I)


def _year_in(question: str, today: date) -> int:
    explicit = re.search(r"\b(20\d{2})\b", question)
    if explicit:
        return int(explicit.group(1))
    if re.search(r"\blast year\b", question, re.I):
        return today.year - 1
    return today.year


def _cost_answer(result: dict, mem: dict) -> str:
    wells = result["wells"]
    when = mem.get("when")
    if mem.get("fallback") or when not in ("yesterday", "today", "last week"):
        which = f"the {len(wells)} wells from your earlier conversation (on {mem.get('discussed_on')})"
    else:
        which = f"the {len(wells)} wells we discussed {when}"
    parts = [f"USD {w['cost_usd']:,.0f} for {w['asset_id']}" + (f" ({w['well_name']})" if w.get("well_name") else "") for w in wells]
    return (
        f"For {which}, maintenance has cost USD {result['total_usd']:,.0f} in {result['year']} "
        f"({'; '.join(parts)}). That counts every maintenance job started that year, scheduled and corrective."
    )


def plan_memory(field: FieldQueries, store: Any, question: str, user_id: str, thread_id: str, today: date) -> tuple[dict | None, str]:
    """Turn 'the three wells we discussed yesterday' into this user's own wells.

    Returns (a finished answer or None, the question to carry on with). Only this user's earlier turns are ever read.
    """
    mem = memory.resolve(store, user_id, thread_id, question, today)
    if mem is None:
        return None, question
    ids = mem["asset_ids"]
    if not ids:
        rows = store.query("SELECT name FROM users WHERE user_id = ?", [user_id])
        who = rows[0]["name"] if rows else "you"
        text = (
            f"I don't have an earlier conversation with {who} about wells, so I can't tell which wells you mean. "
            "Tell me their ids, or ask the question again with the wells named, and I'll look them up."
        )
        return {"visual": "answer", "title": "No earlier conversation", "tool": "memory", "arguments": {"user_id": user_id},
                "result": {"message": text, "asset_ids": []}, "answer": text, "tools_used": [{"tool": "memory", "arguments": {"user_id": user_id}}], "model": False}, question
    if _COST.search(question) and _REPAIR.search(question):
        year = _year_in(question, today)
        result = field.repair_cost(ids, year)
        result["remembered"] = mem
        return _payload("repair_cost", {"asset_ids": ids, "year": year}, result, _cost_answer(result, mem)), question
    return None, f"{question} (the wells meant are {', '.join(ids)})"


# Things people reasonably ask about an oil field that this dataset does not record. Each entry: what to listen for,
# the column names that would prove the data exists, and what it would be called in plain words.
_ABSENT_TOPICS = [
    (re.compile(r"\b(reservoir|pressures?|psi|kpa|bottom-?hole|formation)\b", re.I), ("press", "reservoir", "psi", "kpa"), "reservoir or well pressure"),
    (re.compile(
        r"\b(flow ?rates?|barrels? (?:of oil )?(?:per|a) day|bpd|bopd|production (?:rates?|volumes?|totals?)|oil (?:output|production|volumes?|yield)|how much oil"
        r"|(?:most|least|top|highest|biggest|best|total|more|less)\s+(?:oil|barrels)|(?:produced|pumped|yielded)\s+(?:the\s+)?(?:most|least|more|less|highest|total)\b"
        r"|(?:top|best|biggest|highest)\s+producers?|barrels (?:of oil )?(?:produced|pumped))\b",
        re.I,
    ), ("flow", "bopd", "bpd", "oil_rate", "production_vol"), "oil production volumes"),
    (re.compile(r"\b(water ?cut|gas[- ]oil|gor|salinity|porosity|permeability)\b", re.I), ("water_cut", "gor", "porosity", "permeab", "salin"), "fluid or rock properties"),
    (re.compile(r"\b(well depth|true vertical depth|tvd|measured depth)\b", re.I), ("depth", "tvd"), "well depth"),
]
_HAS = "The records hold weekly air and wellhead temperatures, spills, repairs, regulator events and notes."


def _recorded_columns(field: FieldQueries) -> set[str]:
    rows = field.store.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name IN (" + ", ".join("?" * len(field.QUERYABLE)) + ")",
        list(field.QUERYABLE),
    )
    return {r["column_name"].lower() for r in rows}


def _not_in_the_data(field: FieldQueries, question: str) -> dict | None:
    """If the question needs a kind of data the records do not hold, say so plainly instead of guessing or showing a menu."""
    columns = _recorded_columns(field)
    missing = []
    for pattern, column_hints, label in _ABSENT_TOPICS:
        if pattern.search(question) and not any(hint in column for column in columns for hint in column_hints):
            missing.append(label)
    if not missing:
        return None
    what = " and ".join(missing)
    parts = [f"I can't answer that: this dataset has no data on {what}. {_HAS}"]
    if "reservoir or well pressure" in missing:
        parts.append(
            "The only pressure figures are equipment ratings in the manuals, which describe the hardware, not the reservoir, "
            "and nothing here describes the reservoir, so I can't confirm that the wells share one."
        )
    result = {"missing": missing}
    if "oil production volumes" in missing:
        year = _year_in(question, field.today)
        weeks = field.store.query(
            """
            SELECT asset_id, count(*) AS weeks_inspected, count(*) FILTER (WHERE producing) AS weeks_producing
            FROM inspection_status WHERE year(inspected_at) = ?
            GROUP BY asset_id ORDER BY weeks_producing DESC, asset_id
            """,
            [year],
        )
        if weeks:
            best = weeks[0]["weeks_producing"]
            tied = [w for w in weeks if w["weeks_producing"] == best]
            every = tied[0]["weeks_inspected"] == best
            level = f"in all {best} of their weekly inspections" if every else f"in {best} of their weekly inspections"
            parts.append(
                f"The closest measure is how often a well was found producing: in {year}, {len(tied)} wells were producing {level}, so they tie "
                "and that cannot rank them by oil either."
            )
            result["proxy"] = {"year": year, "most_weeks_producing": best, "wells_at_that_level": len(tied)}
    parts.append("I can show which wells are producing now, or where recent spills happened.")
    return {
        "visual": "answer", "title": "Not in the data", "tool": "data_availability", "arguments": {"asked_about": missing},
        "result": result, "answer": " ".join(parts), "tools_used": [{"tool": "data_availability", "arguments": {"asked_about": missing}}], "model": False,
    }


def plan(field: FieldQueries, question: str) -> dict[str, Any] | None:
    """A finished answer payload, or None when the question needs the usual tools and model."""
    has_well, has_area = bool(_WELL_ID.search(question)), bool(_AREA.search(question))
    types = types_in(question)

    if re.search(r"\bbypass(?:es)?\b", question, re.IGNORECASE):
        result = field.flowline_bypasses()
        return _payload("flowline_bypasses", {}, result, result["summary"])

    absent = _not_in_the_data(field, question)
    if absent:
        return absent

    if re.search(r"\b(letter|commission)\b", question, re.I) and re.search(r"\bspills?\b", question, re.I):
        result = field.spill_letter_compliance()
        if result.get("error"):
            return {"visual": "answer", "title": "The letter", "tool": "spill_letter_compliance", "arguments": {}, "result": result,
                    "answer": result["error"], "tools_used": [{"tool": "spill_letter_compliance", "arguments": {}}], "model": False}
        return _payload("spill_letter_compliance", {}, result, result["summary"])

    pair = readings(question)
    if pair and not has_well:
        air, wellhead = pair
        result = field.evaluate_reading(air, wellhead, types[0] if types else None, types)
        args = {"ambient_c": air, "wellhead_c": wellhead, "well_type": types[0] if types else None}
        return _payload("evaluate_reading", args, result, _reading_answer(result))

    if not has_well and not has_area and _REPAIR_KINDS.search(question):
        result = field.repair_kinds()
        return _payload("repair_kinds", {}, result, result["summary"])

    if not has_well and not has_area and _COUNT_WELLS.search(question):
        result = field.fleet_summary()
        return _payload("fleet_summary", {}, result, _fleet_answer(result, types, question))

    if not has_well and not has_area and not _AGGREGATE.search(question) and _RULE_WORDS.search(question) and (types or _RULE_SUBJECT.search(question)):
        topic = "spill" if _SPILL_WORDS.search(question) and not types else ("temperature" if types else None)
        result = field.well_type_rules(None, topic)
        result["asked_types"] = types
        args = {"well_type": types[0] if len(types) == 1 else None, "topic": topic}
        return _payload("well_type_rules", args, result, _rules_answer(result))
    return None


def _payload(tool: str, arguments: dict, result: dict, answer: str) -> dict:
    return {
        "visual": "table",
        "title": tool,
        "tool": tool,
        "arguments": arguments,
        "result": result,
        "answer": answer,
        "tools_used": [{"tool": tool, "arguments": arguments}],
        "model": False,
    }
