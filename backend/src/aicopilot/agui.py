"""AG-UI event stream and A2UI surfaces.

POST /api/agui takes an AG-UI RunAgentInput and answers with a server-sent event
stream. The copilot's tool call, its text and its generated UI all travel as
AG-UI events. The UI itself is described as A2UI messages (surfaceUpdate,
dataModelUpdate, beginRendering) carried in a CUSTOM event named "a2ui".
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Callable, Iterator

from aicopilot.actions import suggest_actions

# user_id -> what that user was last looking at. Private to the user.
MEMORY: dict[str, dict[str, Any]] = {}

VISUAL_WORDS = [
    ("heatmap", "heatmap"),
    ("line chart", "line"),
    ("bar chart", "bar"),
    ("trend", "line"),
    ("graph", "line"),
    ("map", "map"),
    ("table", "table"),
]


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def sse(event: dict) -> str:
    event.setdefault("timestamp", int(time.time() * 1000))
    return f"data: {json.dumps(event, default=str)}\n\n"


# ─────────────────────────── conversation context ───────────────────────────


def _asset(text: str) -> str | None:
    match = re.search(r"WELL-\d{4}", text.upper())
    return match.group(0) if match else None


def _named_visual(text: str) -> str | None:
    low = text.lower()
    for word, visual in VISUAL_WORDS:
        if word in low:
            return visual
    return None


def resolve(question: str, memory: dict) -> str:
    """Fill in 'it', 'that well', 'there' from what this user discussed last."""
    low = f" {question.lower()} "
    out = question
    if not _asset(question) and memory.get("asset") and re.search(r"\b(it|its|that well|this well|same well)\b", low):
        out += f" {memory['asset']}"
    if memory.get("area") and not any(a in low for a in ("northeast", "northwest", "southeast", "southwest")):
        if re.search(r"\b(there|same area|that area|this area)\b", low):
            out += f" {memory['area']}"
    return out


# ───────────────────────────── re-visualising ─────────────────────────────


def _rows(payload: dict) -> list[dict]:
    result = payload.get("result") or {}
    for key in ("suggestions", "wells", "spills", "points", "inspections", "pins", "cells", "types", "sops", "breakdown", "rows", "changes"):
        if isinstance(result.get(key), list) and result[key]:
            return result[key]
    return []


def _bar_items(rows: list[dict]) -> tuple[list[dict], str]:
    if not rows:
        return [], ""
    first = rows[0]
    if "downtime_days" in first:
        return [{"label": r["asset_id"], "value": r["downtime_days"]} for r in rows], "days"
    if "spill_est_volume_bbl" in first:
        return [{"label": str(r.get("spill_date") or r.get("asset_id"))[:10], "value": r["spill_est_volume_bbl"]} for r in rows], "bbl"
    if "spills" in first:
        totals: dict[str, float] = {}
        for r in rows:
            totals[r["area_name"]] = totals.get(r["area_name"], 0) + r["spills"]
        return [{"label": k, "value": v} for k, v in totals.items()], "spills"
    if "delta_c" in first:
        return [{"label": str(r.get("asset_id") or r.get("inspection_id")), "value": r["delta_c"]} for r in rows], "°C above air"
    if "wellhead_temp_c" in first:
        return [{"label": str(r["inspected_at"])[:10], "value": r["wellhead_temp_c"]} for r in rows], "°C"
    return [], ""


def _line_points(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if "wellhead_temp_c" in r:
            out.append({"x": str(r.get("inspected_at") or r.get("inspected_on"))[:10], "a": r["wellhead_temp_c"], "b": r.get("ambient_temp_c"), "flag": not r.get("producing", True)})
        elif "spill_est_volume_bbl" in r and (r.get("spill_date") or r.get("inspected_at")):
            out.append({"x": str(r.get("spill_date") or r.get("inspected_at"))[:10], "a": r["spill_est_volume_bbl"], "b": None, "flag": False})
    return out


def _compact_items(payload: dict) -> list[dict]:
    """Turn verbose database rows into a small, operator-friendly list."""
    result = payload.get("result") or {}
    tool = payload.get("tool")
    rows = _rows(payload)
    items: list[dict] = []
    for row in rows[:6]:
        if result.get("did_you_mean"):
            items.append({
                "title": row.get("asset_id"),
                "status": row.get("status"),
                "meta": f"{row.get('area')} · {row.get('delta_c')}°C above air · {row.get('inspection_id')}",
                "detail": row.get("reason"),
                "question": f"How is {row.get('asset_id')} doing?",
            })
        elif tool == "non_producing":
            items.append({
                "title": row.get("asset_id"),
                "status": "Not producing",
                "meta": f"{row.get('delta_c')}°C above air · checked {str(row.get('inspected_at'))[:10]}",
                "detail": row.get("reason"),
                "question": f"How is {row.get('asset_id')} doing?",
            })
        elif tool == "top_downtime":
            items.append({
                "title": row.get("asset_id"),
                "status": f"{row.get('downtime_days')} days",
                "meta": row.get("well_type"),
                "detail": None,
                "question": f"How is {row.get('asset_id')} doing?",
            })
    return items


def _default_visual(payload: dict) -> str:
    """Use a visual only when it makes the answer faster to understand."""
    tool = payload.get("tool")
    result = payload.get("result") or {}
    if tool == "spills_for_well":
        return "spills"
    if tool == "rules_broken" and result.get("breaches") is not None:
        return "rules"
    if tool == "late_containment":
        return "late"
    if result.get("did_you_mean") or tool in {"non_producing", "top_downtime"}:
        return "compact"
    if tool == "well_status" and result.get("asset_id"):
        return "status"
    if tool == "temperature_status" and result.get("latest"):
        return "temperature"
    if tool == "spill_map":
        return "map"
    if tool == "spill_heatmap":
        return "heatmap"
    if tool in {"record_correction", "record_rule", "record_note"}:
        return "table"  # what was changed: before, after, and who said so
    if tool in {"evaluate_reading", "well_type_rules", "fleet_summary", "repair_cost", "repair_kinds", "flowline_bypasses", "spill_letter_compliance", "data_availability"} and _rows(payload):
        return "table"  # one row per well type or group: easier to compare than a sentence
    if tool == "data_query" and len(_rows(payload)) > 1:
        return "table"
    return "answer"


def _table_columns(tool: str | None, rows: list[dict]) -> list[str]:
    preferred = {
        "non_producing": ["asset_id", "delta_c", "inspected_at", "reason"],
        "spills_for_well": ["spill_date", "spill_est_volume_bbl", "inspection_id"],
        "top_downtime": ["asset_id", "well_type", "downtime_days", "last_down_inspection"],
        "timeseries": ["inspected_at", "wellhead_temp_c", "ambient_temp_c", "delta_c", "producing"],
        "repair_kinds": ["equipment", "repair", "kind", "jobs", "wells"],
        "fleet_summary": ["well_type", "wells"],
        "flowline_bypasses": ["Well", "Area", "Bypass in", "Bypass out", "Approval", "Approval valid until", "Status"],
        "spill_letter_compliance": ["Well", "Area", "Spill date", "Barrels", "Clean-up report", "Status", "Why"],
    }.get(tool, [])
    if rows and rows[0].get("asset_id") and not preferred:
        preferred = ["asset_id", "status", "area", "delta_c", "inspection_id"]
    return [key for key in preferred if rows and key in rows[0]]


# ───────────────────────────────── surfaces ─────────────────────────────────


# The model sometimes ends its answer with "Evidence: INS-0001-0191, INS-0001-0192, ..." (and can be cut off
# mid-id). The evidence is shown as clickable chips under the answer, so that tail is removed from the text.
_EVIDENCE_TAIL = re.compile(
    r"\s*[(\[]?\s*(?:\*\*)?(?:evidence|sources?|citations?)\s*(?::\s*(?:\*\*)?|\*\*\s*:)\s*[A-Z]{2,4}-[A-Za-z0-9\-,;:\s.)\]]*$",
    re.I,
)


# ...or ends with a run of ids in any wording ("citing evidence INS-0001-0021, INS-0001-0027, INS-0001-"), which the chips already show.
_ID_RUN = re.compile(r"(?:\b(?:INS|CTX|MNT|REG|RULE)-[A-Za-z0-9\-]*\s*[,;]?\s*(?:and\s+)?){2,}$", re.I)
_LEAD_IN = re.compile(r"[,;(\s]*(?:(?:see|citing|cited|supported by|based on|from|with)\b[^.]{0,60}?)?(?:evidence|sources?)(?:\s+ids?)?\s*:?\s*\)?\s*$", re.I)


def clean_answer(text: str) -> str:
    cleaned = _EVIDENCE_TAIL.sub("", text)
    without_ids = _ID_RUN.sub("", cleaned)
    if without_ids != cleaned:
        without_ids = _LEAD_IN.sub("", without_ids).rstrip(" ,;(")
        if without_ids and without_ids[-1] not in ".!?":
            without_ids += "."
        cleaned = without_ids
    return cleaned.rstrip() or text  # never turn an answer into nothing


def summarize(payload: dict) -> str:
    if payload.get("answer"):
        return clean_answer(payload["answer"])
    r = payload.get("result") or {}
    tool = payload.get("tool")
    if tool == "well_status":
        if r.get("error"):
            return r["error"]
        state = "producing" if r.get("producing") else "not producing"
        return f"{r['asset_id']} is {state}. {r.get('reason', '')}".strip()
    if tool == "non_producing":
        return f"{r['count']} wells in {r['area']} are not producing right now."
    if tool == "timeseries":
        return f"{r['asset_id']} has {len(r['points'])} readings; {len(r['not_producing'])} found it not producing."
    if tool == "spills_for_well":
        return f"{r['asset_id']} has {r['count']} recorded spills totalling {r['total_bbl']} barrels."
    if tool == "downtime_days":
        return f"{r['asset_id']} was down {r['days']} days in {r['year']}. {r['rule']}"
    if tool == "top_downtime":
        top = r["wells"][0] if r["wells"] else None
        if not top:
            return f"No downtime recorded in {r['area']} for that period."
        return f"{top['asset_id']} was down longest in {r['area']}: {top['downtime_days']} days."
    if tool == "spill_map":
        return f"{len(r['pins'])} wells in {r['area']} had spills in the last 90 days."
    if tool == "spill_heatmap":
        return f"Spills by area and month for {r['year']}."
    if tool == "temperature_status" and r.get("summary"):
        return r["summary"]
    if tool == "rules_broken" and r.get("summary"):
        return r["summary"]
    if tool == "late_containment" and r.get("summary"):
        return r["summary"]
    if tool == "data_query":
        rows = r.get("rows") or []
        if not rows:
            return "No records match that question."
        show = lambda row: ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in row.items())
        if len(rows) == 1:
            return show(rows[0]) + "."
        return f"{len(rows)} rows. First: {show(rows[0])}."
    if tool == "fleet_summary":
        return f"The dataset has {r.get('total_wells')} wells."
    return r.get("message", "I need a more specific question.")


def suggestions(payload: dict) -> list[str]:
    r = payload.get("result") or {}
    guesses = r.get("suggestions") if isinstance(r, dict) else None
    if guesses:
        return [f"How is {item['asset_id']} doing?" for item in guesses[:4]]
    tool = payload.get("tool")
    asset = r.get("asset_id") if isinstance(r, dict) else None
    area = r.get("area") if isinstance(r, dict) else None
    out: list[str] = []
    if asset:
        out += [f"Show spills for {asset}", f"Temperature trend for {asset}", f"How many days was {asset} down in 2026?"]
        if tool != "well_status":
            out.insert(0, f"Is {asset} producing right now?")
    if area:
        out += [f"Show spills map for {area}", f"Which wells in {area} are not producing?"]
    if not out:
        out = ["Which wells in Northeast Block 7 are not producing?", "Show spills heatmap for 2026", "Is WELL-0001 producing right now?"]
    return out[:4]


def nice_title(payload: dict) -> str:
    """Model answers carry the raw tool name as the title; give people a sentence."""
    r = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    tool, title = payload.get("tool"), payload.get("title")
    if title and title != tool:
        return title
    asset, area = r.get("asset_id"), r.get("area")
    return {
        "well_status": f"Status of {asset}",
        "non_producing": f"Wells not producing in {area}",
        "timeseries": f"Temperature readings for {asset}",
        "spills_for_well": f"Spills at {asset}" + (f" ({r.get('well_name')})" if r.get("well_name") else ""),
        "downtime_days": f"Downtime for {asset} in {r.get('year')}",
        "top_downtime": f"Most downtime in {area}",
        "spill_map": f"Recent spills in {area}",
        "spill_heatmap": f"Spills by area and month, {r.get('year')}",
        "evaluate_reading": f"Hypothetical reading: {r.get('reading', {}).get('gap_c')} °C above the air",
        "well_type_rules": "Operating rules from the manuals",
        "fleet_summary": "The dataset at a glance",
        "spill_letter_compliance": "Spills covered by the Commission's letter",
        "record_correction": "Correction recorded",
        "record_rule": "Rule recorded",
        "record_note": "Note recorded for everyone",
        "repair_cost": f"Maintenance cost in {r.get('year')}",
        "data_query": "From the field database",
        "temperature_status": f"Temperature at {asset} ({r.get('well_name')})",
        "rules_broken": f"Rules broken by {asset}",
        "late_containment": f"Spills contained late in {area}",
    }.get(tool or "", title or "Answer")


def build_surface(payload: dict, visual: str, surface_id: str) -> list[dict]:
    r = payload.get("result") or {}
    comps: list[dict] = []
    model: dict[str, Any] = {}

    def add(cid: str, kind: str, props: dict) -> str:
        comps.append({"id": cid, "component": {kind: props}})
        return cid

    def text(cid: str, value: str, hint: str = "body") -> str:
        return add(cid, "Text", {"text": {"literalString": value}, "usageHint": hint})

    children = [] if visual == "answer" else [text("title", nice_title(payload), "h2")]
    rows = _rows(payload)

    if visual == "status" and r.get("asset_id"):
        model["status"] = r
        children.append(add("v", "StatusCard", {"data": {"path": "/status"}}))
    elif visual == "temperature" and r.get("latest"):
        model["temperature"] = r
        children.append(add("v", "TemperatureCard", {"data": {"path": "/temperature"}}))
    elif visual == "late":
        model["late"] = {"summary": r.get("summary"), "limit": r.get("limit_hours"), "items": r.get("spills") or []}
        children.append(add("v", "LateList", {"data": {"path": "/late"}}))
    elif visual == "rules":
        model["rules"] = {
            "groups": [
                {"heading": "Broken", "items": r.get("breaches") or []},
                {"heading": "Still pending, not a breach yet", "items": r.get("pending") or []},
                {"heading": "Met", "items": r.get("met") or []},
            ]
        }
        children.append(add("v", "RuleList", {"data": {"path": "/rules"}}))
    elif visual == "spills":
        model["spills"] = {"summary": r.get("summary"), "items": r.get("display") or []}
        children.append(add("v", "SpillList", {"data": {"path": "/spills"}}))
    elif visual == "compact" and _compact_items(payload):
        model["items"] = _compact_items(payload)
        model["total"] = len(rows)
        children.append(add("v", "CompactList", {"items": {"path": "/items"}, "total": {"path": "/total"}}))
    elif visual == "bar" and _bar_items(rows)[0]:
        items, unit = _bar_items(rows)
        model["items"] = items
        children.append(add("v", "BarChart", {"items": {"path": "/items"}, "unit": unit, "itemAction": {"name": "ask", "template": "Is {label} producing right now?"} if items[0]["label"].startswith("WELL-") else None}))
    elif visual == "line" and _line_points(rows):
        pts = _line_points(rows)
        model["points"] = pts
        two = pts[0].get("b") is not None
        children.append(add("v", "LineChart", {"points": {"path": "/points"}, "aLabel": "Wellhead °C" if two else "Barrels", "bLabel": "Air °C" if two else None}))
    elif visual == "map" and isinstance(r.get("pins"), list):
        model["pins"] = r["pins"]
        children.append(add("v", "GeoMap", {"pins": {"path": "/pins"}, "itemAction": {"name": "ask", "template": "Show spills for {asset_id}"}}))
    elif visual == "heatmap" and isinstance(r.get("cells"), list):
        model["cells"] = r["cells"]
        children.append(add("v", "Heatmap", {"cells": {"path": "/cells"}}))
    elif visual == "table" and rows:
        cols = _table_columns(payload.get("tool"), rows)
        if not cols:
            cols = [k for k in rows[0].keys() if not isinstance(rows[0][k], (dict, list))][:5]
        model["rows"] = [
            {k: row.get(k) for k in cols}
            for row in rows
        ]
        children.append(add("v", "DataTable", {"columns": [{"key": c, "label": c.replace("_", " ")} for c in cols], "rows": {"path": "/rows"}, "rowAction": {"name": "ask", "template": "Is {asset_id} producing right now?"} if "asset_id" in cols else None}))
    elif visual != "answer":
        children.append(text("msg", r.get("message") or r.get("error") or "No visual for this answer.", "caption"))

    if r.get("evidence"):
        model["evidence"] = r["evidence"][:40]
        children.append(add("ev", "Evidence", {"ids": {"path": "/evidence"}, "tool": payload.get("tool") or ""}))

    sop_actions = payload.get("suggested_actions") or []
    if sop_actions:
        model["sopActions"] = sop_actions
        children.append(add("sop", "ActionList", {"actions": {"path": "/sopActions"}}))

    # Keep follow-up questions sparse, and hide them when SOP actions are the next step.
    buttons: list[str] = []
    for i, q in enumerate([] if sop_actions else suggestions(payload)[:2]):
        bid = f"btn-q{i}"
        text(f"{bid}-t", q)
        add(bid, "Button", {"child": f"{bid}-t", "primary": True, "action": {"name": "ask", "context": {"question": q}}})
        buttons.append(bid)
    if buttons:
        children.append(add("actions", "Row", {"children": {"explicitList": buttons}, "wrap": True}))

    add("root", "Column", {"children": {"explicitList": children}})
    return [
        {"surfaceUpdate": {"surfaceId": surface_id, "components": comps}},
        {"dataModelUpdate": {"surfaceId": surface_id, "path": "/", "contents": model}},
        {"beginRendering": {"surfaceId": surface_id, "root": "root"}},
    ]


# ───────────────────────────────── the run ─────────────────────────────────


def stream_run(body: dict, compute: Callable[..., dict], remember: Callable[..., None] | None = None) -> Iterator[str]:
    thread_id = body.get("threadId") or _id("thread")
    run_id = body.get("runId") or _id("run")
    user_id = (body.get("state") or {}).get("userId") or body.get("userId") or "U-201"
    all_msgs = [m for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
    msgs = [m for m in all_msgs if m.get("role") == "user"]
    question = (msgs[-1].get("content") if msgs else "") or ""
    last_user = max((i for i, m in enumerate(all_msgs) if m.get("role") == "user"), default=len(all_msgs))
    history = [{"role": m["role"], "content": str(m.get("content") or "")} for m in all_msgs[:last_user]][-6:]
    # "it" and "that well" mean this chat's well; the cross-chat memory lives in the database (memory.py)
    memory = MEMORY.setdefault(f"{user_id}:{thread_id}", {"wells": [], "asset": None, "area": None, "last": None})

    yield sse({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})
    try:
        yield sse({"type": "STEP_STARTED", "stepName": "understand"})
        resolved = resolve(question, memory)
        wanted = _named_visual(question)
        yield sse({"type": "STEP_FINISHED", "stepName": "understand"})

        yield sse({"type": "STEP_STARTED", "stepName": "retrieve"})
        started = time.perf_counter()
        low = f" {question.lower()} "
        reuse = bool(
            wanted
            and memory.get("last")
            and not _asset(question)
            and not any(a in low for a in ("northeast", "northwest", "southeast", "southwest"))
            and re.search(r"\b(it|that|this|them|these|same|again)\b", low)
        )
        # "show it as a table": same data, new visual. No new tool call.
        payload = memory["last"] if reuse else compute(resolved, user_id, history, thread_id)
        elapsed = round((time.perf_counter() - started) * 1000, 1)

        tool = payload.get("tool")
        if tool and not reuse:
            call_id = _id("call")
            yield sse({"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": tool})
            yield sse({"type": "TOOL_CALL_ARGS", "toolCallId": call_id, "delta": json.dumps(payload.get("arguments") or {"question": resolved})})
            yield sse({"type": "TOOL_CALL_END", "toolCallId": call_id})
            result = payload.get("result") or {}
            brief = {k: v for k, v in result.items() if not isinstance(v, list)}
            brief["rows"] = len(_rows(payload))
            yield sse({"type": "TOOL_CALL_RESULT", "messageId": _id("tool"), "toolCallId": call_id, "role": "tool", "content": json.dumps(brief, default=str)})
        yield sse({"type": "STEP_FINISHED", "stepName": "retrieve"})

        r = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if r.get("asset_id"):
            memory["asset"] = r["asset_id"]
            if r["asset_id"] not in memory["wells"]:
                memory["wells"].append(r["asset_id"])
        for w in r.get("wells", []) if isinstance(r.get("wells"), list) else []:
            if w.get("asset_id") and w["asset_id"] not in memory["wells"]:
                memory["wells"].append(w["asset_id"])
        if r.get("area"):
            memory["area"] = r["area"]
        memory["wells"] = memory["wells"][-8:]
        if tool:
            memory["last"] = payload
        yield sse({"type": "STATE_SNAPSHOT", "snapshot": {
            "userId": user_id, "threadId": thread_id,
            "memory": {"asset": memory["asset"], "area": memory["area"], "wells": memory["wells"]},
            "lastTool": tool, "latencyMs": payload.get("elapsed_ms") or elapsed,
            "decisionMs": payload.get("decision_ms"), "answerMs": payload.get("answer_ms"),
            "path": payload.get("path") or ("model" if payload.get("model") else "database"),
        }})

        natural = _default_visual(payload)
        rows = _rows(payload)
        can = {
            "table": bool(rows),
            "bar": bool(_bar_items(rows)[0]),
            "line": bool(_line_points(rows)),
            "map": isinstance(r.get("pins"), list),
            "heatmap": isinstance(r.get("cells"), list),
        }
        visual = wanted if wanted and can.get(wanted) else natural
        payload["suggested_actions"] = suggest_actions(question, payload)
        yield sse({"type": "STEP_STARTED", "stepName": "compose"})
        message_id = _id("msg")
        surface_id = f"surface-{message_id}"
        if (tool and visual != "answer") or payload["suggested_actions"]:
            yield sse({"type": "CUSTOM", "name": "a2ui", "value": {"surfaceId": surface_id, "messageId": message_id, "messages": build_surface(payload, visual, surface_id)}})

        yield sse({"type": "TEXT_MESSAGE_START", "messageId": message_id, "role": "assistant"})
        summary = summarize(payload)
        if remember and not reuse:
            remember(user_id, thread_id, question, summary, payload)
        for word in re.findall(r"\S+\s*", summary):
            yield sse({"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": word})
            time.sleep(0.015)
        yield sse({"type": "TEXT_MESSAGE_END", "messageId": message_id})
        yield sse({"type": "STEP_FINISHED", "stepName": "compose"})
        yield sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})
    except Exception as exc:  # the stream must always end with a terminal event
        yield sse({"type": "RUN_ERROR", "message": str(exc)})
