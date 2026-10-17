"""Compute the final-question facts from test-data. Not part of the app."""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "test-data" / "dataset"
ASSETS = ROOT / "assets"
TODAY = datetime(2027, 1, 15, 23, 59, 59)
DELTA = {"T200": 15.0, "T350": 20.0, "T500": 25.0}
RECALL_LOTS = {"LOT-2409-A", "LOT-2409-C", "LOT-2410-B"}

areas = json.loads((ROOT / "fleet" / "areas.json").read_text(encoding="utf-8"))
area_name = {a["area_id"]: a["name"] for a in areas["areas"]}
area_team = {a["area_id"]: a["team_id"] for a in areas["areas"]}

wells = {}
for path in ASSETS.glob("*/metadata.json"):
    meta = json.loads(path.read_text(encoding="utf-8"))
    wells[meta["asset_id"]] = meta


def well_type_on(asset: str, when: datetime) -> str:
    meta = wells[asset]
    history = meta.get("type_history") or []
    day = when.date()
    for item in history:
        start = datetime.fromisoformat(item["from"]).date() if item.get("from") else day
        end = datetime.fromisoformat(item["to"]).date() if item.get("to") else None
        if day >= start and (end is None or day <= end):
            return item["well_type"]
    return meta["well_type"]


def team_on(asset: str, when: datetime) -> str:
    meta = wells[asset]
    history = meta.get("team_history") or []
    day = when.date()
    for item in history:
        start = datetime.fromisoformat(item["from"]).date() if item.get("from") else day
        end = datetime.fromisoformat(item["to"]).date() if item.get("to") else None
        if day >= start and (end is None or day <= end):
            return item["team_id"]
    return meta.get("team_id") or area_team[meta["area_id"]]


def load_jsonl(name: str):
    rows = []
    for path in ASSETS.glob(f"*/{name}"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


notes = load_jsonl("context.jsonl")
inspections = load_jsonl("inspections.jsonl")
jobs = load_jsonl("maintenance.jsonl")

# corrections
temp_fix = {}
vol_fix = {}
spill_fix = {}
for note in notes:
    if note.get("note_type") == "data_correction" and note.get("inspection_id"):
        field = note.get("field")
        if field == "wellhead_temp_c":
            temp_fix[note["inspection_id"]] = float(note["corrected_value"])
        elif field == "spill_est_volume_bbl":
            vol_fix[note["inspection_id"]] = float(note["corrected_value"])
        elif field == "spill_detected":
            spill_fix[note["inspection_id"]] = str(note["corrected_value"]).lower() in {"true", "1", "yes"}
    elif note.get("note_type") == "correction" and note.get("inspection_id"):
        text = note.get("text", "").lower()
        if "not a" in text and "spill" in text:
            spill_fix[note["inspection_id"]] = False


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


for insp in inspections:
    iid = insp["inspection_id"]
    if iid in temp_fix:
        insp["wellhead_temp_c"] = temp_fix[iid]
    if iid in vol_fix:
        insp["spill_est_volume_bbl"] = vol_fix[iid]
    if iid in spill_fix:
        insp["spill_detected"] = spill_fix[iid]
    insp["_at"] = parse_ts(insp["inspected_at"])
    wh = insp.get("wellhead_temp_c")
    air = insp.get("ambient_temp_c")
    if wh is None or air is None:
        insp["_producing"] = None
    else:
        kind = well_type_on(insp["asset_id"], insp["_at"])
        insp["_producing"] = (wh - air) >= DELTA[kind]
        insp["_type"] = kind

inspections.sort(key=lambda r: (r["asset_id"], r["_at"]))
by_well_insp = defaultdict(list)
for insp in inspections:
    by_well_insp[insp["asset_id"]].append(insp)

for job in jobs:
    job["_start"] = parse_ts(job["started_at"])
    job["_end"] = parse_ts(job["completed_at"]) if job.get("completed_at") else None
by_well_job = defaultdict(list)
for job in jobs:
    by_well_job[job["asset_id"]].append(job)
for items in by_well_job.values():
    items.sort(key=lambda r: r["_start"])

ops = [n for n in notes if n.get("note_type") == "operator_note"]
for note in ops:
    note["_at"] = parse_ts(note["created_at"])
by_well_note = defaultdict(list)
for note in ops:
    by_well_note[note["asset_id"]].append(note)
for items in by_well_note.values():
    items.sort(key=lambda r: r["_at"])


def fmt(moment: datetime) -> str:
    return f"{moment.day} {moment:%b %Y} at {moment:%H:%M}"


def day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b %Y}"


# Q3
GAS = re.compile(r"gas odor|smelled gas|rotten-egg|rotten egg|gas smell|h2s monitor alarmed", re.I)
since = datetime(2026, 11, 1)
gas_rows = []
for note in ops:
    if note["_at"] < since or not GAS.search(note.get("text", "")):
        continue
    deadline_h = 12 if note["_at"] >= datetime(2027, 1, 1) else 24
    candidates = []
    for job in by_well_job[note["asset_id"]]:
        if job["_start"] > note["_at"]:
            candidates.append((job["_start"], job["maintenance_id"], "job"))
    for insp in by_well_insp[note["asset_id"]]:
        if insp.get("method") == "manual" and insp["_at"] > note["_at"]:
            candidates.append((insp["_at"], insp["inspection_id"], "inspection"))
    candidates.sort()
    if candidates:
        at, rid, kind = candidates[0]
        hours = (at - note["_at"]).total_seconds() / 3600
        met = hours <= deadline_h + 1e-6
    else:
        at = rid = kind = None
        hours = None
        met = False
    gas_rows.append({
        "well": note["asset_id"],
        "area": area_name[wells[note["asset_id"]]["area_id"]],
        "reported": fmt(note["_at"]),
        "note": note["note_id"],
        "quote": note["text"],
        "response": f"{rid} at {fmt(at)}" if rid else "none",
        "hours": None if hours is None else round(hours, 1),
        "deadline": deadline_h,
        "met": "Met" if met else "Missed",
    })
gas_rows.sort(key=lambda r: r["reported"])
print("Q3", len(gas_rows), "met", sum(r["met"] == "Met" for r in gas_rows), "missed", sum(r["met"] == "Missed" for r in gas_rows))
for row in gas_rows:
    print(f"{row['well']} | {row['area']} | {row['reported']} | {row['response']} | {row['hours']} | {row['deadline']}h | {row['met']} | {row['note']}")

# Q5
print("\nQ5")
letter_start = datetime(2026, 6, 1)
ne = {"NE-07", "NE-08"}
for insp in inspections:
    area = wells[insp["asset_id"]]["area_id"]
    if area not in ne or insp["_at"] < letter_start:
        continue
    if not insp.get("spill_detected"):
        continue
    vol = insp.get("spill_est_volume_bbl")
    if vol is None or vol < 5:
        continue
    cleanup = None
    for job in by_well_job[insp["asset_id"]]:
        if job.get("triggered_by_inspection_id") == insp["inspection_id"] or (
            job.get("report_file") and insp["inspection_id"] in (job.get("issue") or "")
        ):
            cleanup = job
            break
    if cleanup is None:
        for job in by_well_job[insp["asset_id"]]:
            if job.get("report_file") and job["_start"] >= insp["_at"] and "spill" in (job.get("issue") or "").lower():
                cleanup = job
                break
    report_text = ""
    if cleanup and cleanup.get("report_file"):
        report = ASSETS / insp["asset_id"] / cleanup["report_file"]
        if report.exists():
            report_text = report.read_text(encoding="utf-8")
    compliant = "Soil samples sent to the lab." in report_text
    reason = "Soil samples sent to the lab." if compliant else (
        "Clean-up report has no lab-sample sentence." if cleanup else "No clean-up job found."
    )
    print(
        f"{insp['asset_id']} | {area_name[area]} | {insp['inspection_id']} {day(insp['_at'])} | {vol} | "
        f"{cleanup['maintenance_id'] if cleanup else 'none'} | {'Yes' if compliant else 'No'} — {reason}"
    )

# Q6
print("\nQ6")
SHUT = re.compile(r"\bshut[\s-]?in\b", re.I)
RESTART = re.compile(r"\brestarted\b|\bback on production\b|\breturned to production\b", re.I)


def status_note(text: str):
    if SHUT.search(text):
        return "shut in"
    if RESTART.search(text):
        return "restarted"
    return None


disagree = []
for asset, insps in by_well_insp.items():
    latest = insps[-1]
    if latest["_producing"] is None:
        continue
    relevant = [n for n in by_well_note[asset] if status_note(n["text"])]
    if not relevant:
        continue
    note = relevant[-1]
    claim = status_note(note["text"])
    insp_says = "producing" if latest["_producing"] else "not producing"
    note_says = "not producing" if claim == "shut in" else "producing"
    if insp_says == note_says:
        continue
    if note["_at"] > latest["_at"]:
        if claim == "shut in":
            now = "Not producing oil (shut in)"
            why = "The shut-in note is later than the inspection."
        else:
            now = "Producing oil (unconfirmed)"
            why = "The restart note is later than the inspection, and no inspection has confirmed it."
    else:
        now = "Producing oil" if latest["_producing"] else "Not producing oil"
        why = "The inspection is later than the operator note, so the inspection stands."
    disagree.append((asset, area_name[wells[asset]["area_id"]], latest, note, insp_says, claim, now, why))
disagree.sort()
print("count", len(disagree))
for asset, area, latest, note, insp_says, claim, now, why in disagree:
    print(
        f"{asset} | {area} | {latest['inspection_id']} {day(latest['_at'])} shows {insp_says} | "
        f"{fmt(note['_at'])} {claim}: {note['text']} | {now} | {why}"
    )

# Q7
print("\nQ7")
window_start = datetime(2026, 12, 15)
ABNORMAL = re.compile(
    r"rattl|squeal|knock|grind|vibrat|hunting|tripp|underload|overload|stuffing box|"
    r"belt (?:slip|off|broken)|shaking|noisy|noise|screaming|won't start|will not start|"
    r"stalled|seized|running hot|amps (?:high|low|hunting)|leaking (?:oil|at)|"
    r"gearbox|bearing|polished rod|brittle|cracked",
    re.I,
)
NORMAL = re.compile(r"nothing to report|pumping normally|pad dry|no issues", re.I)
candidates = []
for note in ops:
    if not (window_start <= note["_at"] <= TODAY):
        continue
    text = note.get("text", "")
    if NORMAL.search(text) or not ABNORMAL.search(text):
        continue
    later = [
        j for j in by_well_job[note["asset_id"]]
        if j["_start"] > note["_at"] and "spill" not in (j.get("issue") or "").lower()
    ]
    if later:
        continue
    candidates.append(note)
by_asset = defaultdict(list)
for note in candidates:
    by_asset[note["asset_id"]].append(note)
print("wells", len(by_asset), "notes", len(candidates))
scored = []
for asset, items in by_asset.items():
    prior = [
        j for j in by_well_job[asset]
        if datetime(2026, 10, 1) <= j["_start"] <= window_start
        and "spill" not in (j.get("issue") or "").lower()
    ]
    latest_insp = by_well_insp[asset][-1] if by_well_insp[asset] else None
    producing = latest_insp["_producing"] if latest_insp else None
    score = len(items) * 3 + min(len(prior), 3) + (2 if producing is False else 0)
    scored.append((score, asset, items, prior, producing))
scored.sort(key=lambda r: (-r[0], r[1]))
for score, asset, items, prior, producing in scored[:15]:
    print("---", asset, area_name[wells[asset]["area_id"]], "score", score, "producing", producing, "prior", len(prior))
    for note in items:
        print(" ", note["note_id"], fmt(note["_at"]), note["text"])
    for job in prior[-2:]:
        print("  prior", job["maintenance_id"], job.get("issue"), day(job["_start"]))

# Q8 / Q9 recall
print("\nRECALL")
PARTS = re.compile(r"\*\*Parts installed:\*\*\s*(.+)")
LOT = re.compile(r"lot (LOT-[\w-]+)", re.I)
installs = []
for job in jobs:
    if not job.get("report_file"):
        continue
    report = ASSETS / job["asset_id"] / job["report_file"]
    if not report.exists():
        continue
    text = report.read_text(encoding="utf-8")
    match = PARTS.search(text)
    if not match or "RP-175" not in match.group(1):
        continue
    lot = LOT.search(match.group(1))
    if not lot:
        continue
    installs.append({
        "asset": job["asset_id"],
        "job": job["maintenance_id"],
        "at": job["_start"],
        "lot": lot.group(1),
        "line": match.group(1).strip(),
        "cost": (job.get("cost_usd") or {}).get("total"),
        "recalled": lot.group(1) in RECALL_LOTS,
    })
latest = {}
for item in sorted(installs, key=lambda r: (r["asset"], r["at"])):
    if item["at"] <= TODAY:
        latest[item["asset"]] = item
still = [item for item in latest.values() if item["recalled"]]
still.sort(key=lambda r: r["asset"])
print("rp175 installs", len(installs), "still recalled", len(still))
for item in still:
    meta = wells[item["asset"]]
    print(
        f"{item['asset']} | {area_name[meta['area_id']]} | {item['job']} | {item['lot']} | "
        f"{day(item['at'])} | cost {item['cost']} | {item['line']}"
    )

# Q7 heat trace for Q9
print("\nHEAT")
HEAT = "heat trace check"
dec_start, dec_end = datetime(2026, 12, 1), datetime(2026, 12, 31, 23, 59)
misses = []
for insp in inspections:
    if not (dec_start <= insp["_at"] <= dec_end):
        continue
    air = insp.get("ambient_temp_c")
    if air is None or air > -5 or not insp["_producing"]:
        continue
    due = insp["_at"] + timedelta(days=7)
    done = any(
        HEAT in (job.get("issue") or "").lower() and insp["_at"] < job["_start"] <= due
        for job in by_well_job[insp["asset_id"]]
    )
    if not done:
        misses.append(insp)
print("cold producing inspections missing heat trace", len(misses))
seen = set()
for insp in misses:
    if insp["asset_id"] in seen:
        continue
    seen.add(insp["asset_id"])
    print(insp["asset_id"], area_name[wells[insp["asset_id"]]["area_id"]], insp["inspection_id"], day(insp["_at"]), "air", insp.get("ambient_temp_c"))

# Q9 team
print("\nTEAM-NW")
nw = [asset for asset in wells if team_on(asset, TODAY) == "TEAM-NW"]
print("team wells", len(nw))
nw_area = [asset for asset in wells if wells[asset]["area_id"] == "NW-12"]
print("NW-12 wells", len(nw_area))
print("team not in NW-12", sorted(set(nw) - set(nw_area))[:20], "count", len(set(nw) - set(nw_area)))
print("NW-12 not team", sorted(set(nw_area) - set(nw))[:20], "count", len(set(nw_area) - set(nw)))

# Q11
print("\nQ11")
year_start = datetime(2026, 1, 1)
temps = defaultdict(list)
deltas = defaultdict(list)
for insp in inspections:
    if insp["_at"] < year_start or insp["_producing"] is not True:
        continue
    area = area_name[wells[insp["asset_id"]]["area_id"]]
    temps[area].append(insp["wellhead_temp_c"])
    deltas[area].append(insp["wellhead_temp_c"] - insp["ambient_temp_c"])
for area in sorted(temps):
    print(
        area,
        "n", len(temps[area]),
        "median wellhead", round(statistics.median(temps[area]), 1),
        "mean wellhead", round(statistics.mean(temps[area]), 1),
        "median delta", round(statistics.median(deltas[area]), 1),
    )

# Monthly wellhead medians, producing inspections in 2026.
monthly = defaultdict(lambda: defaultdict(list))
for insp in inspections:
    if insp["_at"] < year_start or insp["_at"] >= datetime(2027, 1, 1) or insp["_producing"] is not True:
        continue
    area = area_name[wells[insp["asset_id"]]["area_id"]]
    monthly[insp["_at"].month][area].append(insp["wellhead_temp_c"])
print("\nQ11 months")
hottest = defaultdict(int)
for month in range(1, 13):
    meds = {area: statistics.median(vals) for area, vals in monthly[month].items() if vals}
    winner = max(meds, key=meds.get)
    hottest[winner] += 1
    print(month, winner, round(meds[winner], 1), "vs next", round(sorted(meds.values())[-2], 1))
print("hottest months", dict(hottest))

# Q10
print("\nQ10")
LONGER = re.compile(
    r"stayed shut in for about (\d+) days? longer than the repair itself because (.+?)\.",
    re.I,
)
WAITED = re.compile(
    r"(The company workover rig|.+?'s workover rig) was busy on other wells, so the well waited about (\d+) days?",
    re.I,
)
both = 0
q10 = []
for job in jobs:
    if job["_start"].year != 2026 or not job.get("report_file"):
        continue
    report = ASSETS / job["asset_id"] / job["report_file"]
    if not report.exists():
        continue
    text = report.read_text(encoding="utf-8")
    longer = LONGER.search(text)
    waited = WAITED.search(text)
    if longer and waited:
        both += 1
    if longer:
        days = int(longer.group(1))
        because = longer.group(2).lower()
        if "part" in because:
            reason = "Parts"
        elif "crew" in because:
            reason = "Crews"
        elif "rain" in because or "weather" in because or "road" in because:
            reason = "Weather"
        elif "contractor" in because:
            reason = "Contractors"
        else:
            reason = "Other: " + because
    elif waited:
        days = int(waited.group(2))
        who = waited.group(1).lower()
        reason = "Crews" if who.startswith("the company") else "Contractors"
    else:
        continue
    if days <= 0:
        continue
    q10.append((area_name[wells[job["asset_id"]]["area_id"]], reason, days))
print("jobs", len(q10), "reports with both sentences", both)
by_area_reason = defaultdict(lambda: defaultdict(list))
for area, reason, days in q10:
    by_area_reason[area][reason].append(days)
for area in sorted(by_area_reason):
    total = sum(len(v) for v in by_area_reason[area].values())
    bits = []
    for reason in ("Parts", "Crews", "Contractors", "Weather"):
        vals = by_area_reason[area][reason]
        share = 100 * len(vals) / total if total else 0
        typical = statistics.median(vals) if vals else None
        bits.append(f"{reason} {len(vals)} {share:.0f}% med {typical}")
    print(area, "n", total, " | ".join(bits))
fleet_reason = defaultdict(list)
for area, reason, days in q10:
    fleet_reason[reason].append(days)
print("fleet", {k: (len(v), statistics.median(v)) for k, v in fleet_reason.items()})

# Recall pumps: producing now and last repair
print("\nPUMPS")


def current_status(asset: str):
    latest = by_well_insp[asset][-1]
    relevant = [n for n in by_well_note[asset] if status_note(n["text"]) and n["_at"] > latest["_at"]]
    if relevant:
        claim = status_note(relevant[-1]["text"])
        if claim == "shut in":
            return "Not producing oil (shut in)", latest
        return "Producing oil (unconfirmed)", latest
    return ("Producing oil" if latest["_producing"] else "Not producing oil"), latest


for item in still:
    if item["asset"] == "WELL-0333":
        continue
    status, latest = current_status(item["asset"])
    repairs = [j for j in by_well_job[item["asset"]] if j["_start"] <= TODAY]
    last = repairs[-1]
    print(item["asset"], status, latest["inspection_id"], day(latest["_at"]), "last", last["maintenance_id"], last.get("issue"), day(last["_start"]))
