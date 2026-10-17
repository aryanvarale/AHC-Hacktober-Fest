"""SOP actions the copilot can hand to another agent.

These are suggestions, not automatic field commands. Execute records a dispatch
and names the agent that would carry it out.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

DISPATCHES: list[dict[str, Any]] = []

_CATALOG = {
    "alarms_on": {"label": "Turn on the alarms", "verb": "Execute", "agent": "Alarm agent", "sop": "Immediate notice of a fire"},
    "hydrants_on": {"label": "Activate the water hydrants", "verb": "Activate", "agent": "Firewater agent", "sop": "Immediate notice of a fire"},
    "incident_command": {"label": "Call incident command", "verb": "Notify", "agent": "HSE agent", "sop": "Major incident"},
    "shut_in": {"label": "Shut in the well", "verb": "Execute", "agent": "Well control agent", "sop": "Stop the source"},
    "contain_spill": {"label": "Contain the spill", "verb": "Execute", "agent": "Containment agent", "sop": "SR-02 · within 4 hours"},
    "notify_regulator": {"label": "Phone the regulator", "verb": "Notify", "agent": "Regulator agent", "sop": "SR-01 · written notice within 24 hours"},
    "repair_order": {"label": "Open a repair work order", "verb": "Dispatch", "agent": "Maintenance agent", "sop": "Repair the cause"},
    "drone_check": {"label": "Send a drone to re-inspect", "verb": "Dispatch", "agent": "Drone agent", "sop": "Confirm the site"},
    "notify_supervisor": {"label": "Notify the area supervisor", "verb": "Notify", "agent": "Production agent", "sop": "RC-03 · down more than 14 days"},
}


_DEFINITION = re.compile(
    r"\b(what do you mean|what does|what is|what's|whats|define|definition|explain|meaning of|mean by)\b"
)
_HAPPENING = re.compile(
    r"\b(there is|there's|theres|we have|reported|happening|right now|ongoing|just found|discovered|active)\b"
)


def suggest_actions(question: str, payload: dict) -> list[dict]:
    text = f" {question.lower()} "
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    tool = payload.get("tool")
    asset = _asset(question, result)
    ids: list[str] = []
    # A definition or explanation is not an incident, so it gets no actions.
    if _DEFINITION.search(text) and not _HAPPENING.search(text):
        return []

    fire = _HAPPENING.search(text) and re.search(r"\b(fire|flame|flames|smoke|explosion|burning)\b", text)
    if fire:
        ids += ["alarms_on", "hydrants_on", "shut_in", "incident_command"]
    spill = _HAPPENING.search(text) and re.search(r"\b(spill|release)\b", text)
    if spill:
        ids += ["shut_in", "contain_spill", "notify_regulator", "repair_order", "drone_check"]
    if tool == "temperature_status" and result.get("state") == "too hot":
        ids += ["shut_in", "repair_order", "drone_check"]
    down = (
        (tool == "well_status" and result.get("producing") is False)
        or (tool == "temperature_status" and result.get("state") == "too cold")
        or (tool == "downtime_days" and bool(result.get("asset_id")))
    )
    if down and not spill and not fire:
        ids += ["repair_order", "drone_check"]
        days = result.get("days")
        if isinstance(days, (int, float)) and days > 14:
            ids.append("notify_supervisor")

    seen: set[str] = set()
    actions = []
    for action_id in ids:
        if action_id in seen:
            continue
        seen.add(action_id)
        spec = _CATALOG[action_id]
        where = asset or result.get("area") or "the site"
        actions.append({
            "id": action_id,
            "label": spec["label"],
            "verb": spec["verb"],
            "agent": spec["agent"],
            "sop": spec["sop"],
            "asset_id": asset,
            "detail": f"{spec['agent']} · {where}",
        })
    return actions


def dispatch_action(action_id: str, asset_id: str | None, user_id: str) -> dict:
    spec = _CATALOG.get(action_id)
    if spec is None:
        return {"error": "That action is not in the SOP list."}
    record = {
        "action_id": action_id,
        "label": spec["label"],
        "verb": spec["verb"],
        "agent": spec["agent"],
        "sop": spec["sop"],
        "asset_id": asset_id,
        "user_id": user_id,
        "status": "dispatched",
        "message": f"Sent to the {spec['agent']}.",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    DISPATCHES.append(record)
    return record


def _asset(question: str, result: dict) -> str | None:
    match = re.search(r"WELL-\d+", question, re.IGNORECASE)
    if match:
        return match.group(0).upper()
    if result.get("asset_id"):
        return result["asset_id"]
    guesses = result.get("suggestions") if isinstance(result.get("suggestions"), list) else []
    if guesses and guesses[0].get("asset_id"):
        return guesses[0]["asset_id"]
    wells = result.get("wells") if isinstance(result.get("wells"), list) else []
    if len(wells) == 1 and wells[0].get("asset_id"):
        return wells[0]["asset_id"]
    return None
