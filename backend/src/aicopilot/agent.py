"""Tool-calling loop against the OpenAI-compatible Qwen server."""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from typing import Any
from urllib import error, request

from aicopilot.config import Settings
from aicopilot.queries import FieldQueries

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "well_status",
            "description": "Current status of one well: producing or not, temperatures, inspector, recorded status, open repairs, and notes.",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string", "description": "Well id, for example WELL-0001"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "non_producing",
            "description": "Wells in one geographic area that are not producing oil right now.",
            "parameters": {
                "type": "object",
                "properties": {"area_name": {"type": "string"}},
                "required": ["area_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "timeseries",
            "description": "Wellhead and air temperature readings for one well over recent weeks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "weeks": {"type": "integer"},
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spills_for_well",
            "description": "Real oil spills for one well after corrections are applied.",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "downtime_days",
            "description": "Days a well was not producing in a calendar year. Each non-producing weekly inspection counts as 7 days.",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}, "year": {"type": "integer"}},
                "required": ["asset_id", "year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_downtime",
            "description": "Wells in an area with the most downtime between two dates, inclusive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "area_name": {"type": "string"},
                    "start": {"type": "string", "description": "YYYY-MM-DD"},
                    "end": {"type": "string", "description": "YYYY-MM-DD"},
                    "limit": {"type": "integer"},
                },
                "required": ["area_name", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spill_map",
            "description": "Wells in an area with a real spill in the last N days.",
            "parameters": {
                "type": "object",
                "properties": {"area_name": {"type": "string"}, "days": {"type": "integer"}},
                "required": ["area_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "late_containment",
            "description": "Spills in an area that took longer to contain than the SOP limit. The limit is already known.",
            "parameters": {
                "type": "object",
                "properties": {"area_name": {"type": "string"}, "days": {"type": "integer"}},
                "required": ["area_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rules_broken",
            "description": "Rules a well broke in the last 12 months, which are still open, and work that is pending but not yet a breach.",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "temperature_status",
            "description": "Last temperature of one well, its normal range, and since what day and time it has been outside that range.",
            "parameters": {
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spill_heatmap",
            "description": "Spill counts by geographic area and month for one year.",
            "parameters": {
                "type": "object",
                "properties": {"year": {"type": "integer"}},
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "well_type_rules",
            "description": "What each well type needs to count as producing (gap above air), its wellhead alarm limit, and the spill reporting and containment deadlines, read from the manuals with corrections applied.",
            "parameters": {
                "type": "object",
                "properties": {
                    "well_type": {"type": "string", "description": "For example T200 or T350. Leave out for all types."},
                    "topic": {"type": "string", "description": "temperature or spill. Leave out for both."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_query",
            "description": "Any other question about the whole dataset or many wells that no other tool answers: counts, totals, averages, rankings and comparisons. A read-only SQL query is written and run for you.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_reading",
            "description": "Is a made-up reading producing? Takes an air and a wellhead temperature (no real well) and gives the gap and the verdict under every well type's rule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ambient_c": {"type": "number"},
                    "wellhead_c": {"type": "number"},
                    "well_type": {"type": "string"},
                },
                "required": ["ambient_c", "wellhead_c"],
            },
        },
    },
]

_VISUAL = {
    "well_status": "status",
    "non_producing": "table",
    "timeseries": "line",
    "spills_for_well": "table",
    "downtime_days": "answer",
    "top_downtime": "bar",
    "spill_map": "map",
    "spill_heatmap": "heatmap",
    "temperature_status": "temperature",
    "rules_broken": "rules",
    "late_containment": "late",
    "well_type_rules": "table",
    "evaluate_reading": "table",
    "fleet_summary": "table",
    "data_query": "table",
}

# These two answer from rules, not from a well, so a well id in the question must not replace them.
_PURE_TOOLS = {"well_type_rules", "evaluate_reading", "data_query"}


_ASSET_TOOLS = {"well_status", "timeseries", "spills_for_well", "downtime_days", "temperature_status", "rules_broken"}


def ask_model(field: FieldQueries, question: str, settings: Settings, history: list[dict] | None = None) -> dict:
    """Gemma picks the tool. Qwen writes every answer from that tool result."""
    today = settings.today
    context = _context(history)
    earlier = " ".join(m["content"] for m in (history or [])[-6:] if m.get("role") == "user")
    request_text = f"{earlier} {question}"
    decision_ms, choice = _decide(question, settings, today, context)
    exact = _exact_well(question, field)
    loose = None if exact else _loose_well(question, field)
    assumed = None
    if loose:
        suggestions = field.suggest_wells(loose)
        exact_matches = [s for s in suggestions if s.get("exact")]
        if len(exact_matches) == 1:
            exact = exact_matches[0]["asset_id"]
            assumed = {"said": loose, "asset_id": exact, "well_name": exact_matches[0].get("well_name")}
            loose = None
    if exact and re.search(r"\b(rule|rules|broken|violation|breach)\b", question, re.IGNORECASE):
        choice = {"tool": "rules_broken", "arguments": {"asset_id": exact}}
    area = _area_name(question)
    if area and re.search(r"\b(contain|contained|containment)\b", question, re.IGNORECASE):
        days_match = re.search(r"(\d+)\s*days", question, re.IGNORECASE)
        choice = {"tool": "late_containment", "arguments": {"area_name": area, "days": int(days_match.group(1)) if days_match else 90}}
    named = list(dict.fromkeys(w.upper() for w in re.findall(r"\bWELL-\d{3,4}\b", question, re.IGNORECASE)))
    if len(named) >= 2 and choice["tool"] not in {"none"} | _PURE_TOOLS:
        choice = {"tool": "data_query", "arguments": {}}   # the single-well tools would answer only the first one
    if exact and choice["tool"] not in _PURE_TOOLS:
        if choice["tool"] in _ASSET_TOOLS:
            choice["arguments"]["asset_id"] = exact
        else:
            choice = _tool_for_exact(request_text, exact)
    if choice["tool"] in _ASSET_TOOLS and not exact and not loose and not _refers_back(question):
        # the router only found this well in the earlier conversation, and this question does not point back at it
        choice = {"tool": "none", "arguments": {}}
    if choice["tool"] == "data_query" or (choice["tool"] == "none" and not loose and _looks_like_data_question(question)):
        data = _data_query(field, question, settings, today, context)
        if data is not None:
            used = [{"tool": "data_query", "arguments": {"sql": data["sql"]}, "result": data}]
            answer_ms, answer = _answer(question, "data_query", data, settings, today, context)
            answer = _ground(question, "data_query", data, answer, settings, today, context)
            payload = _payload(answer, used)
            payload["decision_ms"] = round(decision_ms, 1)
            payload["answer_ms"] = round(answer_ms, 1)
            payload["router"] = settings.router_model
            return payload
        if choice["tool"] == "data_query":
            choice = {"tool": "none", "arguments": {}}
    if choice["tool"] == "none" and not loose:
        answer_ms, answer = _chat(question, settings, today, context)
        payload = _payload(answer, [])
        payload["decision_ms"] = round(decision_ms, 1)
        payload["answer_ms"] = round(answer_ms, 1)
        payload["router"] = settings.router_model
        return payload
    if loose and choice["tool"] in _ASSET_TOOLS | {"none"}:
        suggestions = field.suggest_wells(loose)
        result = {"query": loose.upper(), "did_you_mean": True, "suggestions": suggestions, "count": len(suggestions)}
        tool = choice["tool"] if choice["tool"] in _ASSET_TOOLS else "well_status"
        used = [{"tool": tool, "arguments": {"asset_id": loose.upper()}, "result": result}]
    else:
        result = _run_tool(field, choice["tool"], choice["arguments"], today)
        if assumed:
            result = {**result, "assumed": assumed}
        used = [{"tool": choice["tool"], "arguments": choice["arguments"], "result": result}]
    answer_ms, answer = _answer(question, used[-1]["tool"], result, settings, today, context)
    answer = _align_visual_answer(question, answer)
    answer = _ground(question, used[-1]["tool"], result, answer, settings, today, context)
    if result.get("did_you_mean") and result.get("suggestions") and not answer.lower().startswith("did you mean"):
        answer = _mean_line(result["suggestions"])
    payload = _payload(answer, used)
    if result.get("did_you_mean"):
        payload["title"] = "Did you mean"
        payload["visual"] = "table"
    payload["decision_ms"] = round(decision_ms, 1)
    payload["answer_ms"] = round(answer_ms, 1)
    payload["router"] = settings.router_model
    return payload


_BACK = re.compile(
    r"\b(it|its|it's|that|those|these|them|same|there|again|also|too|previous|earlier|above|other|former|latter)\b"
    r"|^\s*(and|what about|how about|then|so)\b",
    re.IGNORECASE,
)


def _refers_back(question: str) -> bool:
    """True when the question leans on the earlier conversation ("what about its spills?")."""
    return bool(_BACK.search(question))


_CLAIM = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*C\b|degrees?\b|bbl\b|barrels?\b|days?\b|hours?\b|hrs?\b|%|psi\b)", re.IGNORECASE)
_ANY_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


_IDS = re.compile(r"\b(?:WELL-\d{3,4}|INS-\d{4}-\d{4}|CTX-\d{4}-\d{3}|MNT-\d{4}-[\w-]+|REG-\d{4}-\d{3})\b", re.IGNORECASE)


_WELL_NAMES = re.compile(r"\b[A-Z]{2}-\d{2} Well \d{3}\b")


def _unsupported(answer: str, result: dict, question: str) -> list[str]:
    """Things the answer states that neither the tool result nor the question contains.

    Figures with a unit count as supported if they are in the result or the question, or are the difference of
    two numbers there (a gap between two temperatures, for example). Well and record ids must appear verbatim:
    a query that did not return a well id must not be answered with one.
    """
    source = json.dumps(result, default=str) + " " + question
    problems = [i for i in dict.fromkeys(_IDS.findall(answer) + _WELL_NAMES.findall(answer)) if i.upper() not in source.upper()]
    claims = [(float(m.group(1)), m.group(0).strip()) for m in _CLAIM.finditer(answer)]
    if claims:
        pool = [float(x) for x in _ANY_NUMBER.findall(source)][:80]
        allowed = set(pool)
        for i, x in enumerate(pool):
            for y in pool[i + 1:]:
                allowed.add(round(abs(x - y), 2))
        problems += [text for value, text in claims if not any(abs(abs(value) - abs(p)) <= 0.051 for p in allowed)]
    return problems


def _ground(question: str, tool: str, result: dict, answer: str, settings: Settings, today: date, context: str) -> str:
    """Keep the model honest: redo an answer that states a figure the data does not contain, and if it still does, use the plain summary."""
    if result.get("did_you_mean"):
        return answer
    bad = _unsupported(answer, result, question)
    if not bad:
        return answer
    _, retry = _answer(question, tool, result, settings, today, context, avoid=", ".join(bad))
    retry = _align_visual_answer(question, retry)
    if not _unsupported(retry, result, question):
        return retry
    from aicopilot.agui import summarize
    return summarize({"tool": tool, "result": result})


_DATA_QUESTION = re.compile(
    r"\b(how many|how much|number of|count|total|average|mean|median|most|least|highest|lowest|top \d*|rank(?:ing)?|"
    r"compare|versus|vs\.?|per|each|every|percent(?:age)?|sum|cost|cheapest|expensive|which (?:wells?|areas?|types?|technicians?|contractors?|teams?)|list)\b",
    re.IGNORECASE,
)


def _looks_like_data_question(question: str) -> bool:
    """A question about the data that the router found no single-well or single-area tool for."""
    return bool(_DATA_QUESTION.search(question))


def _clean_sql(text: str) -> str:
    cleaned = re.sub(r"(?is)^\s*```(?:sql)?|```\s*$", "", text.strip()).strip()
    return cleaned.rstrip(";").strip()


def _data_query(field: FieldQueries, question: str, settings: Settings, today: date, context: str) -> dict | None:
    """Have the model write one SELECT, run it on the allow-listed tables, and give back the rows (None if it cannot)."""
    prompt = (
        f"You write one read-only DuckDB SELECT statement that answers an operator's question about an oil field. Today is {today.isoformat()}.\n"
        "Use only these tables and views:\n" + field.schema_text() + "\n"
        "Notes: inspection_status is every inspection with corrections applied; its producing column says whether the well was producing at that inspection, "
        "and well_now is each well's latest inspection. spills_effective holds real spills after corrections. "
        "wells.well_type is the well type, and areas.name is the area name (join on area_id). "
        "Definitions: a well's DOWNTIME is 7 days for every inspection_status row where producing is false (maintenance.downtime_hours is repair time, not downtime). "
        "A FINE is a reg_events row with fine_usd > 0; other regulator events are notices and visits. A REPORTABLE spill is 5 barrels or more. "
        "REPAIRS are maintenance rows; maintenance_type separates corrective from scheduled work. "
        "When ranking or grouping by a text column, leave out NULL values (WHERE the column IS NOT NULL) unless the question is about missing data. "
        "Always return asset_id and name when the answer is about wells, and the area name when it is about areas, so every named thing comes from the rows. "
        "Give tables short aliases that are not SQL keywords (w, i, m, a, s, r, u) and never use words such as is, as, on, in, by or to as an alias. "
        "Name every output column clearly and do not use SELECT *. "
        "If the question cannot be answered from these tables, reply with exactly NOT_AVAILABLE. Reply with the SQL only.\n"
        + (f"Earlier conversation:\n{context}\n" if context else "")
        + f"Question: {question}\nSQL:"
    )
    for _ in range(3):
        sql = _clean_sql(_generate(settings.base_url, settings.model, prompt, settings.api_key, 400, thinking=False))
        if not sql or sql.upper().startswith("NOT_AVAILABLE"):
            print(f"[data_query] model said NOT_AVAILABLE for: {question!r} (sql={sql!r})", file=sys.stderr, flush=True)
            return None
        result = field.run_select(sql)
        if not result.get("error"):
            print(f"[data_query] ok {result['row_count']} rows: {' '.join(sql.split())[:200]}", file=sys.stderr, flush=True)
            return result
        print(f"[data_query] rejected: {result['error']} | {' '.join(sql.split())[:200]}", file=sys.stderr, flush=True)
        hint = " Do not use SQL keywords as aliases; use w, i, m, a." if "Parser" in result["error"] else ""
        prompt += f" {sql}\nThat failed: {result['error']}.{hint}\nWrite a corrected SQL query only.\nSQL:"
    return None


def _mean_line(suggestions: list[dict]) -> str:
    parts = []
    for item in suggestions:
        parts.append(
            f"{item['asset_id']} in {item['area']} is {item['status']} "
            f"({item['delta_c']}°C above air, {item['inspection_id']}). {item['reason']}"
        )
    return "Did you mean " + " ".join(parts)


def _exact_well(question: str, field: FieldQueries) -> str | None:
    match = re.search(r"\bWELL-(\d+)\b", question, re.IGNORECASE)
    if not match:
        return None
    asset = f"WELL-{match.group(1)}"
    return asset if field.well_exists(asset) else None


def _tool_for_exact(question: str, asset: str) -> dict:
    text = question.lower()
    if "spill" in text:
        return {"tool": "spills_for_well", "arguments": {"asset_id": asset}}
    if "trend" in text or "chart" in text or "graph" in text:
        return {"tool": "timeseries", "arguments": {"asset_id": asset}}
    if re.search(r"\b(rule|rules|broken|violation|breach)\b", text):
        return {"tool": "rules_broken", "arguments": {"asset_id": asset}}
    if re.search(r"temp|hot|cold|overheat|normal", text):
        return {"tool": "temperature_status", "arguments": {"asset_id": asset}}
    if "downtime" in text or re.search(r"\bdays\b", text):
        year = re.search(r"20\d{2}", question)
        return {"tool": "downtime_days", "arguments": {"asset_id": asset, "year": int(year.group(0)) if year else 2026}}
    return {"tool": "well_status", "arguments": {"asset_id": asset}}


def _area_name(question: str) -> str | None:
    text = question.lower()
    names = {
        "northeast block 7": "Northeast Block 7",
        "northeast block 8": "Northeast Block 8",
        "southwest block 23": "Southwest Block 23",
        "southwest block 24": "Southwest Block 24",
    }
    return next((name for key, name in names.items() if key in text), None)


def _loose_well(question: str, field: FieldQueries) -> str | None:
    """A well token that is not an exact id, such as well-001 or Tank 001 for WELL-0001."""
    match = re.search(r"\b(?:well|tank|asset|unit)[-\s]?(\d+)\b", question, re.IGNORECASE)
    if not match:
        return None
    if field.well_exists(f"WELL-{match.group(1)}"):
        return None
    return match.group(0)


def _run_tool(field: FieldQueries, name: str, args: dict, today: date) -> dict:
    if name in _ASSET_TOOLS:
        asset = str(args.get("asset_id") or "").upper()
        if asset and not field.well_exists(asset):
            suggestions = field.suggest_wells(asset)
            return {"query": asset, "did_you_mean": True, "suggestions": suggestions, "count": len(suggestions)}
    if name == "well_status":
        return field.well_status(str(args["asset_id"]).upper())
    if name == "non_producing":
        return field.non_producing(args["area_name"])
    if name == "timeseries":
        return field.timeseries(str(args["asset_id"]).upper(), int(args.get("weeks") or 13))
    if name == "spills_for_well":
        return field.spills_for_well(str(args["asset_id"]).upper())
    if name == "downtime_days":
        return field.downtime_days(str(args["asset_id"]).upper(), int(args["year"]))
    if name == "top_downtime":
        start = date.fromisoformat(args["start"])
        end = date.fromisoformat(args["end"])
        return field.top_downtime(args["area_name"], start, end, int(args.get("limit") or 3))
    if name == "spill_map":
        return field.spill_map(args["area_name"], int(args.get("days") or 90))
    if name == "spill_heatmap":
        return field.spill_heatmap(int(args.get("year") or today.year))
    if name == "temperature_status":
        return field.temperature_status(str(args["asset_id"]).upper())
    if name == "rules_broken":
        return field.rules_broken(str(args["asset_id"]).upper())
    if name == "late_containment":
        return field.late_containment(args["area_name"], int(args.get("days") or 90))
    if name == "well_type_rules":
        return field.well_type_rules(args.get("well_type") or None, args.get("topic") or None)
    if name == "evaluate_reading":
        return field.evaluate_reading(float(args["ambient_c"]), float(args["wellhead_c"]), args.get("well_type") or None)
    return {"error": f"Unknown tool {name}"}


def _context(history: list[dict] | None) -> str:
    turns = []
    for item in (history or [])[-6:]:
        content = str(item.get("content") or "").strip()
        if content:
            who = "Operator" if item.get("role") == "user" else "Copilot"
            turns.append(f"{who}: {content[:400]}")
    return "\n".join(turns)


def _align_visual_answer(question: str, answer: str) -> str:
    """The model writes prose; A2UI renders the requested visual separately."""
    match = re.search(r"\b(heatmap|map|table|chart|graph)\b", question, re.IGNORECASE)
    if not match:
        return answer
    cleaned, removed = re.subn(
        r"^\s*(?:I\s+)?(?:cannot|can't|am unable to|do not have the ability to)\s+"
        r"(?:generate|create|make|display|show|render)?\s*(?:a\s+)?(?:visual\s+)?"
        r"(?:chart|graph|map|heatmap|table)s?[^.!?]*[.!?]\s*",
        "",
        answer,
        count=1,
        flags=re.IGNORECASE,
    )
    if not removed:
        return answer
    kind = match.group(1).lower()
    return f"Here is the requested {kind}. {cleaned.strip()}".strip()


def _payload(answer: str, used: list[dict]) -> dict:
    last = used[-1] if used else None
    result = last["result"] if last else {"message": answer}
    tool = last["tool"] if last else None
    return {
        "visual": _VISUAL.get(tool, "answer"),
        "title": tool or "Answer",
        "tool": tool,
        "arguments": last["arguments"] if last else None,
        "result": result,
        "answer": answer,
        "tools_used": [{"tool": item["tool"], "arguments": item["arguments"]} for item in used],
        "model": True,
    }


def _decide(question: str, settings: Settings, today: date, context: str = "") -> tuple[float, dict]:
    names = ", ".join(item["function"]["name"] for item in _TOOLS)
    prompt = (
        f"Today is {today.isoformat()}. Last 4 months is 2026-06-16 to 2026-10-16. "
        "Areas: Northeast Block 7, Northeast Block 8, Southwest Block 23, Southwest Block 24. "
        f"Tools: {names}. "
        "Copy a well id exactly as written. WELL-0001 stays WELL-0001, and a short id such as well-001 stays WELL-001. "
        "temperature_status answers last temperature, whether it is normal, and since when it is abnormal. "
        "rules_broken answers which rules a well broke and what is still open. "
        "late_containment finds spills contained slower than the SOP limit. It already knows the limit. Do not ask for it. "
        "timeseries is only for a temperature chart or trend over weeks. "
        "well_status, spills_for_well, timeseries, downtime_days and temperature_status need asset_id. "
        "If the question corrects or completes an earlier message, such as 'I meant Tank 001', "
        "answer the earlier request with the corrected id. "
        "non_producing and spill_map and top_downtime need area_name. "
        "top_downtime also needs start and end as YYYY-MM-DD. spill_heatmap needs year. "
        "Use tool none for greetings, thanks, small talk, questions about you, or anything "
        "that is not a request for well, area, spill, downtime or temperature data. "
        "data_query is for any other question about the whole dataset or about many wells that the tools above do not answer, such as counts, totals, averages, rankings and comparisons. Use data_query for those, not none. "
        "Use tool none when a required well id or area is missing from the question, unless it is a whole-dataset question. "
        "Never take a well id from the earlier conversation unless the question clearly refers back to it. "
        "evaluate_reading is for a made-up reading with an air and a wellhead temperature and no real well: pass ambient_c, wellhead_c and well_type if given. "
        "well_type_rules is for what a well type needs to be producing, its alarm limit, or the spill deadlines: pass well_type such as T350, or leave it out. "
        "Reply with JSON only: {\"tool\":\"name\",\"arguments\":{...}}. "
        + (f"\nEarlier conversation:\n{context}\n" if context else "")
        + f"Question: {question}"
    )
    started = _clock()
    text = _generate(settings.router_base_url, settings.router_model, prompt, settings.api_key, 80, thinking=False)
    choice = _valid(_parse_choice(text))
    if choice is None:
        text = _generate(settings.base_url, settings.model, prompt, settings.api_key, 80, thinking=False)
        choice = _valid(_parse_choice(text)) or {"tool": "none", "arguments": {}}
    return (_clock() - started) * 1000, choice


_REQUIRED = {
    "well_status": ("asset_id",),
    "non_producing": ("area_name",),
    "timeseries": ("asset_id",),
    "spills_for_well": ("asset_id",),
    "downtime_days": ("asset_id", "year"),
    "top_downtime": ("area_name", "start", "end"),
    "spill_map": ("area_name",),
    "spill_heatmap": ("year",),
    "temperature_status": ("asset_id",),
    "rules_broken": ("asset_id",),
    "late_containment": ("area_name",),
    "well_type_rules": (),
    "data_query": (),
    "evaluate_reading": ("ambient_c", "wellhead_c"),
}


def _valid(choice: dict) -> dict | None:
    tool = choice.get("tool")
    if tool == "none":
        return {"tool": "none", "arguments": {}}
    if tool not in _REQUIRED:
        return None
    args = choice.get("arguments") or {}
    if any(args.get(key) in (None, "") for key in _REQUIRED[tool]):
        return {"tool": "none", "arguments": {}}
    return {"tool": tool, "arguments": args}


def _chat(question: str, settings: Settings, today: date, context: str = "") -> tuple[float, str]:
    prompt = (
        f"You are the Asset Intelligence Copilot for an oil field. Today is {today.isoformat()}. "
        "You answer from field records about wells: whether a well is producing, downtime, spills, "
        "temperatures, maps and spill heatmaps by area. The records hold wells only, not tanks. "
        "Reply briefly and naturally. Keep the earlier conversation in mind and do not ask again for "
        "something the operator already said. If the operator asked for data but left out a well id or "
        "area, ask for it. Do not invent any field facts.\n"
        + (f"Earlier conversation:\n{context}\n" if context else "")
        + f"Operator: {question}"
    )
    started = _clock()
    text = _generate(settings.base_url, settings.model, prompt, settings.api_key, 160, thinking=False)
    return (_clock() - started) * 1000, text.strip()


def _answer(question: str, tool: str, result: dict, settings: Settings, today: date, context: str = "", avoid: str = "") -> tuple[float, str]:
    suggestions = result.get("suggestions") if result.get("did_you_mean") else None
    if suggestions is not None:
        if suggestions:
            prompt = (
                f"Today is {today.isoformat()}. The operator named a well id that is not in the records. "
                "Reply in two or three sentences. The first words must be 'Did you mean'. "
                "Name each suggested well, say whether it is producing, and cite its inspection id and reason. "
                "Use only these suggestions. Do not say the lookup failed and do not invent a well.\n"
                f"Question: {question}\nSuggestions: {json.dumps(suggestions, default=str)}"
            )
        else:
            prompt = (
                f"Today is {today.isoformat()}. No stored well is close to the id in the question. "
                "Say that well ids look like WELL-0001 and ask which well they mean. Do not invent a well.\n"
                f"Question: {question}"
            )
    else:
        assumed = result.get("assumed")
        prompt = (
            f"Today is {today.isoformat()}. Answer the operator from this {tool} result in at most 70 words. "
            "Answer exactly what they asked first, including every day and time they asked for. "
            "Refer to a well by its id, and add its name in brackets only if the result gives that well's name. "
            "Give dates with the time of day. Use plain language, not raw field names. "
            "If the result has an inspections_note, include it in your own words; otherwise do not mention it. "
            "If the operator said above normal but the reading is below normal, say clearly that it is below normal. "
            "Make sure every sentence agrees with the others. "
            "If the result gives a time limit, use it. Never ask the operator for that limit. "
            "The UI renders requested charts, graphs, tables, maps and heatmaps from this result. "
            "When a visual was requested, introduce it briefly; never claim you cannot generate or display it. "
            "Cite only the evidence ids needed, inside a sentence. Never end with a list of evidence ids: they are shown separately. If it says there is no recorded reason, say that. Do not invent a cause.\n"
            + (
                f"The operator said \"{assumed['said']}\", which is not in the records. Begin with: "
                f"\"There is no {assumed['said']} in the records, so I used {assumed['asset_id']} ({assumed['well_name']}).\"\n"
                if assumed else ""
            )
            + (f"Earlier conversation:\n{context}\n" if context else "")
            + f"Question: {question}\nResult: {json.dumps(result, default=str)}"
        )
    started = _clock()
    if tool == "data_query":
        prompt += "\nThe result comes from the SQL shown in it, run on the field database. Say exactly what was counted or measured. If it returned no rows, say no records match. Never add a figure that is not in the rows.\n"
    if avoid:
        prompt += f"\nYour previous draft stated things that are not in the result ({avoid}). Use only numbers and ids that appear in the result; if it has no well id, name the well by its name only.\n"
    text = _generate(settings.base_url, settings.model, prompt, settings.api_key, 180, thinking=False)
    return (_clock() - started) * 1000, text.strip()


def _generate(base_url: str, model: str, prompt: str, api_key: str, max_tokens: int, thinking: bool) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    req = request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with request.urlopen(req, timeout=120) as response:
            data = json.loads(response.read().decode())
    except error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Model request failed ({exc.code}): {detail}") from exc
    message = data["choices"][0]["message"]
    return message.get("content") or ""


def _parse_choice(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return {"tool": "", "arguments": {}}
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"tool": "", "arguments": {}}
    return {"tool": parsed.get("tool") or "", "arguments": parsed.get("arguments") or {}}


def _clock() -> float:
    from time import perf_counter
    return perf_counter()
