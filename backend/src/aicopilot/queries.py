"""Question tools. Each one returns a small summary plus the evidence ids
the answer is allowed to cite. The model is expected to call these, not scan files."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from aicopilot.adapters import DuckDBStore
from aicopilot.config import DATA_DIR


def _well_rank(num: str, digits: str, padded: str) -> tuple | None:
    """Lower sorts first. An id that only needs zero-padding outranks every other guess."""
    if num == padded or num == digits:
        return (0, 0, num)
    if len(digits) >= 3 and num.startswith(digits):
        return (1, 0, num)
    if len(digits) >= 3 and num.endswith(digits):
        return (2, 0, num)
    try:
        distance = abs(int(num) - int(digits))
    except ValueError:
        return None
    if len(digits) >= 3 and distance <= 5:
        return (3, distance, num)
    return None


def _spill_class(barrels: float) -> str:
    if barrels < 1:
        return "Minor"
    if barrels < 5:
        return "Moderate"
    if barrels < 25:
        return "Reportable"
    return "Major"


_BYPASS_IN = re.compile(
    r"\b(?:bypass installed|had the bypass installed|put the bypass in|temporary bypass in place|bypass in place)\b",
    re.IGNORECASE,
)
_BYPASS_OUT = re.compile(
    r"\b(?:bypass removed|bypass pulled|took the bypass out)\b",
    re.IGNORECASE,
)


def _bypass_approvals(sops: Path) -> dict[str, dict]:
    """The MOC that currently authorizes a bypass on each well, after extensions replace earlier expiry dates."""
    coverage: dict[str, dict] = {}
    if not sops.exists():
        return coverage
    for path in sorted(sops.glob("moc-*.md")):
        text = path.read_text(encoding="utf-8")
        if "flowline bypass" not in text.lower():
            continue
        expires = re.search(r"expires on (\d{1,2} \w+ \d{4})", text, re.IGNORECASE)
        if not expires:
            continue
        memo = path.stem.upper()
        expiry = datetime.strptime(expires.group(1), "%d %b %Y").date()
        for well in re.findall(r"WELL-\d{4}", text):
            coverage[well] = {"memo": memo, "expires": expiry}
    return coverage


def _bypass_row(asset_id: str, area: str, opened: dict, closed: dict | None, approvals: dict[str, dict], today: date) -> dict:
    approval = approvals.get(asset_id)
    started = date.fromisoformat(opened["created_at"][:10])
    ended = date.fromisoformat(closed["created_at"][:10]) if closed else None
    if approval is None:
        status = "Not approved"
    elif (ended or today) > approval["expires"]:
        status = "Expired"
    else:
        status = "Allowed"
    return {
        "Well": asset_id,
        "Area": area,
        "Bypass in": _day(started),
        "Bypass out": "still in" if ended is None else _day(ended),
        "Approval": approval["memo"] if approval else "none",
        "Approval valid until": _day(approval["expires"]) if approval else "—",
        "Status": status,
        "evidence": [opened["note_id"]] + ([closed["note_id"]] if closed else []),
    }


def _day(value: Any) -> str:
    moment = date.fromisoformat(str(value)[:10])
    return f"{moment.day} {moment:%b %Y}"


def _when(value: Any) -> str:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return f"{moment.day} {moment:%b %Y} at {moment:%H:%M}"


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 4)
    return value


def _n_(value: float) -> str:
    return f"{round(float(value), 2):g}"


def _clean(row: dict) -> dict:
    return {key: _iso(value) for key, value in row.items()}


class FieldQueries:
    def __init__(self, store: DuckDBStore, today: date):
        self.store = store
        self.today = today

    def users(self) -> list[dict]:
        return self.store.query("SELECT user_id, name, role, team_id FROM users ORDER BY name")

    def areas(self) -> list[dict]:
        return self.store.query("SELECT area_id, name FROM areas ORDER BY name")

    def well_status(self, asset_id: str) -> dict:
        rows = self.store.query(
            """
            SELECT s.*, u.name AS inspector_name
            FROM well_now s
            LEFT JOIN users u ON u.user_id = s.inspector_user_id
            WHERE s.asset_id = ?
            """,
            [asset_id],
        )
        if not rows:
            return self._unknown_well(asset_id)
        status = _clean(rows[0])
        status["recorded_status_stale"] = (
            (status["recorded_status"] == "active" and not status["producing"])
            or (status["recorded_status"] == "down" and status["producing"])
        )
        status["open_repairs"] = [
            _clean(row) for row in self.store.query(
                """
                SELECT maintenance_id, issue, equipment_kind, started_at, completed_at, maintenance_type
                FROM maintenance
                WHERE asset_id = ?
                  AND started_at <= ?
                  AND (completed_at IS NULL OR completed_at > ?)
                ORDER BY started_at
                """,
                [asset_id, self.today.isoformat(), self.today.isoformat()],
            )
        ]
        status["active_notes"] = [
            _clean(row) for row in self.store.query(
                """
                SELECT n.note_id, n.note_type, n.text, n.author_user_id, u.name AS author_name,
                       n.valid_from, n.valid_to
                FROM notes n
                LEFT JOIN users u ON u.user_id = n.author_user_id
                WHERE n.asset_id = ?
                  AND n.valid_from <= ?
                  AND (n.valid_to IS NULL OR n.valid_to >= ?)
                ORDER BY n.created_at
                """,
                [asset_id, self.today.isoformat(), self.today.isoformat()],
            )
        ]
        status["upcoming_notes"] = [
            _clean(row) for row in self.store.query(
                """
                SELECT n.note_id, n.note_type, n.text, u.name AS author_name, n.valid_from, n.valid_to
                FROM notes n
                LEFT JOIN users u ON u.user_id = n.author_user_id
                WHERE n.asset_id = ? AND n.valid_from > ?
                ORDER BY n.valid_from
                """,
                [asset_id, self.today.isoformat()],
            )
        ]
        status["corrections"] = self._corrections_on(status)
        note = (status.get("notes") or "").strip()
        if status["corrections"] and note and status["producing"] and re.search(r"not moving|no stroke|shut|stopped|down|idle|stuck", note, re.IGNORECASE):
            status["correction_conflict"] = (
                f"The inspector's note \"{note}\" ({status.get('inspector_name')}) disagrees with the corrected reading, which says the well is producing."
            )
        status["reason"] = self._downtime_reason(status)
        status["evidence"] = [status["inspection_id"], *[n["note_id"] for n in status["active_notes"]], *[n["note_id"] for n in status["upcoming_notes"]]]
        return status

    def _corrections_on(self, row: dict) -> list[dict]:
        """The corrections applied to one inspection: what, from what, to what, by whom, why. The original stays on file."""
        out = []
        for column, by, reason, original, now in (
            ("wellhead_temp_c", "temp_corrected_by", "temp_correction_reason", "temp_original_value", "wellhead_temp_c"),
            ("spill_est_volume_bbl", "volume_corrected_by", "volume_correction_reason", "volume_original_value", "spill_est_volume_bbl"),
            ("spill_detected", "spill_corrected_by", "spill_correction_reason", "spill_detected_original", "spill_detected"),
        ):
            if row.get(by):
                names = self.store.query("SELECT name FROM users WHERE user_id = ?", [row[by]])
                out.append({
                    "field": column, "original": row.get(original), "corrected": row.get(now),
                    "corrected_by": names[0]["name"] if names else row[by], "reason": row.get(reason),
                })
        return out

    def temperature_status(self, asset_id: str, limit: int = 26) -> dict:
        """Last reading, the normal range, and when the current out-of-range streak began."""
        status = self.well_status(asset_id)
        if status.get("did_you_mean"):
            return status
        alarm = self.store.query(
            "SELECT numeric_value FROM rules WHERE parameter = 'temp_alarm_c' AND scope_value = ?",
            [status["well_type"]],
        )
        max_c = alarm[0]["numeric_value"] if alarm else None
        rows = [
            _clean(row) for row in self.store.query(
                """
                SELECT inspection_id, inspected_at, ambient_temp_c, wellhead_temp_c, delta_c, producing_delta_c
                FROM inspection_status
                WHERE asset_id = ? AND inspected_at::DATE <= ?
                ORDER BY inspected_at DESC
                LIMIT ?
                """,
                [asset_id, self.today.isoformat(), limit],
            )
        ]

        def problem(row: dict) -> str | None:
            if max_c is not None and row["wellhead_temp_c"] > max_c:
                return "too hot"
            if row["delta_c"] < row["producing_delta_c"]:
                return "too cold"
            return None

        latest = rows[0]
        state = problem(latest)
        streak = []
        for row in rows:
            if problem(row) != state or state is None:
                break
            streak.append(row)
        since = streak[-1] if streak else None
        last_normal = next((row for row in rows if problem(row) is None), None)

        def reading(row: dict | None) -> dict | None:
            if row is None:
                return None
            return {
                "inspection_id": row["inspection_id"],
                "at": _when(row["inspected_at"]),
                "wellhead_c": round(row["wellhead_temp_c"], 1),
                "air_c": round(row["ambient_temp_c"], 1),
                "above_air_c": round(row["delta_c"], 1),
            }

        normal = f"at least {status['producing_delta_c']:g} °C above air temperature"
        if max_c is not None:
            normal += f" and no hotter than {max_c:g} °C"
        cause = status["reason"] if state else None
        summary = (
            f"{asset_id} ({status['well_name']}) last read {latest['wellhead_temp_c']:.1f} °C at the wellhead on "
            f"{_when(latest['inspected_at'])}, {latest['delta_c']:.1f} °C above air ({latest['ambient_temp_c']:.1f} °C). "
            f"Normal is {normal}. "
        )
        state_text = {"too cold": "below the normal range (too cold)", "too hot": "above the normal range (too hot)"}.get(state or "")
        if state:
            summary += (
                f"It has been {state_text} since the inspection on {_when(since['inspected_at'])} "
                f"({len(streak)} inspection{'s' if len(streak) != 1 else ''} in a row)."
            )
            if last_normal:
                summary += f" The last normal reading was on {_when(last_normal['inspected_at'])}."
        else:
            summary += "The latest reading is within the normal range."
        return {
            "asset_id": asset_id,
            "well_name": status["well_name"],
            "area": status.get("area_name"),
            "state": state or "normal",
            "state_text": state_text or "within the normal range",
            "normal_range": normal,
            "latest": reading(latest),
            "abnormal_since": reading(since),
            "readings_out_of_range": len(streak),
            "last_normal": reading(last_normal),
            "cause": cause,
            "inspections_note": (
                "Inspectors keep checking a shut-in well every week, so readings continue after the shut-in."
                if cause and re.search(r"shut|repair|replace", cause, re.IGNORECASE) else None
            ),
            "summary": summary,
            "evidence": [r["inspection_id"] for r in [latest, since, last_normal] if r] + [n["note_id"] for n in status["active_notes"]],
        }

    def well_exists(self, asset_id: str) -> bool:
        rows = self.store.query("SELECT 1 AS ok FROM wells WHERE asset_id = ? LIMIT 1", [asset_id])
        return bool(rows)

    def suggest_wells(self, raw: str, limit: int = 4) -> list[dict]:
        """Closest real wells when an operator shortens or mistypes an id."""
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if not digits:
            return []
        padded = digits.zfill(4) if len(digits) <= 4 else digits
        rows = self.store.query("SELECT asset_id FROM well_now ORDER BY asset_id")
        ranked: list[tuple[tuple, str]] = []
        for row in rows:
            num = str(row["asset_id"]).split("-")[-1]
            rank = _well_rank(num, digits, padded)
            if rank is not None:
                ranked.append((rank, row["asset_id"]))
        ranked.sort()
        if ranked and ranked[0][0][0] == 0:
            ranked = [item for item in ranked if item[0][0] == 0]
        suggestions = []
        for rank, asset_id in ranked[:limit]:
            status = self.well_status(asset_id)
            if status.get("did_you_mean") or status.get("error"):
                continue
            suggestions.append({
                "asset_id": status["asset_id"],
                "well_name": status.get("well_name"),
                "exact": rank[0] == 0,
                "area": status.get("area_name"),
                "status": "producing" if status["producing"] else "not producing",
                "delta_c": round(status["delta_c"], 1),
                "inspection_id": status["inspection_id"],
                "reason": status["reason"],
            })
        return suggestions

    def _unknown_well(self, asset_id: str) -> dict:
        suggestions = self.suggest_wells(asset_id)
        return {
            "query": asset_id,
            "did_you_mean": True,
            "suggestions": suggestions,
            "count": len(suggestions),
        }

    def _downtime_reason(self, status: dict) -> str:
        if status["open_repairs"]:
            job = status["open_repairs"][-1]
            return f"Repair {job['maintenance_id']}, {job['issue']}, started {str(job['started_at'])[:10]}, still open"
        notes = [n for n in status["active_notes"] if n["note_type"] != "correction"]
        if notes:
            note = notes[-1]
            return f"{note['note_id']} by {note['author_name']}: {note['text']}"
        if not status["producing"] and status.get("notes"):
            return f"No recorded reason. The inspector noted \"{status['notes']}\"."
        if not status["producing"]:
            return "No recorded reason."
        return "The latest inspection shows the well producing."

    def non_producing(self, area_name: str) -> dict:
        rows = self.store.query(
            """
            SELECT asset_id, well_type, delta_c, recorded_status, inspection_id, inspected_at, notes, producing
            FROM well_now
            WHERE area_name = ? AND NOT producing
            ORDER BY asset_id
            """,
            [area_name],
        )
        wells = []
        for row in rows:
            item = self.well_status(row["asset_id"])
            wells.append({
                "asset_id": item["asset_id"],
                "well_name": item.get("well_name"),
                "well_type": item["well_type"],
                "delta_c": round(item["delta_c"], 1),
                "recorded_status": item["recorded_status"],
                "recorded_status_stale": item["recorded_status_stale"],
                "inspected_at": str(item["inspected_at"])[:10],
                "reason": item["reason"],
                "inspection_id": item["inspection_id"],
            })
        gone = self.store.query(
            """
            SELECT asset_id, inspection_id, wellhead_temp_original, wellhead_temp_c, ambient_temp_c, producing_delta_c,
                   temp_corrected_by, temp_correction_reason
            FROM well_now
            WHERE area_name = ? AND producing AND temp_corrected_by IS NOT NULL
              AND (wellhead_temp_original - ambient_temp_c) < producing_delta_c
            ORDER BY asset_id
            """,
            [area_name],
        )
        left_list = []
        for row in gone:
            names = self.store.query("SELECT name FROM users WHERE user_id = ?", [row["temp_corrected_by"]])
            left_list.append({
                "asset_id": row["asset_id"], "inspection_id": row["inspection_id"],
                "original_wellhead_c": row["wellhead_temp_original"], "corrected_wellhead_c": row["wellhead_temp_c"],
                "corrected_by": names[0]["name"] if names else row["temp_corrected_by"], "reason": row["temp_correction_reason"],
            })
        result = {
            "area": area_name,
            "count": len(wells),
            "wells": wells,
            "evidence": [w["inspection_id"] for w in wells] + [w["inspection_id"] for w in left_list],
            "visual": "table",
        }
        if left_list:
            result["corrected_to_producing"] = left_list  # on the list before a correction, producing now
        return result

    def timeseries(self, asset_id: str, weeks: int = 13) -> dict:
        start = self.today - timedelta(days=weeks * 7)
        rows = self.store.query(
            """
            SELECT inspection_id, inspected_at, ambient_temp_c, wellhead_temp_c, delta_c, producing
            FROM inspection_status
            WHERE asset_id = ? AND inspected_at::DATE > ? AND inspected_at::DATE <= ?
            ORDER BY inspected_at
            """,
            [asset_id, start.isoformat(), self.today.isoformat()],
        )
        points = [_clean(row) for row in rows]
        return {
            "asset_id": asset_id,
            "points": points,
            "not_producing": [p["inspection_id"] for p in points if not p["producing"]],
            "evidence": [p["inspection_id"] for p in points],
            "visual": "line",
        }

    def spills_for_well(self, asset_id: str, years: int = 4) -> dict:
        start = date(self.today.year - years, self.today.month, self.today.day)
        rows = self.store.query(
            """
            SELECT inspection_id, inspected_at::DATE AS spill_date, spill_est_volume_bbl,
                   spill_correction_reason
            FROM spills_effective
            WHERE asset_id = ? AND inspected_at::DATE > ? AND inspected_at::DATE <= ?
            ORDER BY inspected_at
            """,
            [asset_id, start.isoformat(), self.today.isoformat()],
        )
        spills = [_clean(row) for row in rows]
        total = round(sum(row["spill_est_volume_bbl"] or 0 for row in spills), 1)
        names = self.store.query("SELECT name FROM wells WHERE asset_id = ?", [asset_id])
        display = []
        for row in spills:
            volume = row["spill_est_volume_bbl"] or 0
            kind = _spill_class(volume)
            display.append({
                "when": _day(row["spill_date"]),
                "volume": f"{volume:g} barrels",
                "spill_class": kind,
                "inspection_id": row["inspection_id"],
            })
        reportable = sum(1 for row in spills if (row["spill_est_volume_bbl"] or 0) >= 5)
        if not spills:
            summary = "No spills recorded in the last 4 years."
        else:
            summary = f"{len(spills)} spill{'s' if len(spills) != 1 else ''}, {total:g} barrels in total."
            summary += (
                f" {reportable} reached the 5 barrel reporting limit."
                if reportable else " None reached the 5 barrel reporting limit."
            )
        return {
            "asset_id": asset_id,
            "well_name": names[0]["name"] if names else None,
            "count": len(spills),
            "total_bbl": total,
            "summary": summary,
            "display": display,
            "spills": spills,
            "evidence": [row["inspection_id"] for row in spills],
            "visual": "table",
        }

    def downtime_days(self, asset_id: str, year: int) -> dict:
        rows = self.store.query(
            """
            SELECT inspection_id, inspected_at::DATE AS inspected_on, delta_c,
                   wellhead_temp_c, ambient_temp_c
            FROM inspection_status
            WHERE asset_id = ?
              AND NOT producing
              AND year(inspected_at) = ?
            ORDER BY inspected_at
            """,
            [asset_id, year],
        )
        inspections = [_clean(row) for row in rows]
        return {
            "asset_id": asset_id,
            "year": year,
            "days": len(inspections) * 7,
            "inspections": inspections,
            "evidence": [row["inspection_id"] for row in inspections],
            "rule": "Each non-producing weekly inspection counts as 7 days. A missed inspection adds none.",
        }

    def top_downtime(self, area_name: str, start: date, end: date, limit: int = 3) -> dict:
        rows = self.store.query(
            """
            SELECT asset_id, well_type, count(*) * 7 AS downtime_days,
                   max(inspected_at) AS last_down_inspection
            FROM inspection_status
            WHERE area_name = ?
              AND NOT producing
              AND inspected_at::DATE >= ?
              AND inspected_at::DATE <= ?
            GROUP BY asset_id, well_type
            ORDER BY downtime_days DESC, last_down_inspection DESC
            LIMIT ?
            """,
            [area_name, start.isoformat(), end.isoformat(), limit + 1],
        )
        ranked = [_clean(row) for row in rows]
        return {
            "area": area_name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "wells": ranked[:limit],
            "next": ranked[limit] if len(ranked) > limit else None,
            "evidence": [row["asset_id"] for row in ranked[:limit]],
            "visual": "bar",
        }

    def spill_map(self, area_name: str, days: int = 90) -> dict:
        start = self.today - timedelta(days=days)
        rows = self.store.query(
            """
            SELECT asset_id, well_name, inspected_at::DATE AS spill_date, spill_est_volume_bbl, lat, lon, inspection_id
            FROM spills_effective
            WHERE area_name = ?
              AND inspected_at::DATE > ?
              AND inspected_at::DATE <= ?
            ORDER BY inspected_at
            """,
            [area_name, start.isoformat(), self.today.isoformat()],
        )
        pins = []
        for row in rows:
            pin = _clean(row)
            volume = pin["spill_est_volume_bbl"] or 0
            pin["when"] = _day(pin["spill_date"])
            pin["spill_class"] = _spill_class(volume)
            pins.append(pin)
        return {"area": area_name, "days": days, "pins": pins, "evidence": [p["inspection_id"] for p in pins], "visual": "map"}

    def spill_heatmap(self, year: int) -> dict:
        rows = self.store.query(
            """
            SELECT area_name, month(inspected_at) AS month, count(*) AS spills
            FROM spills_effective
            WHERE year(inspected_at) = ?
            GROUP BY area_name, month
            ORDER BY area_name, month
            """,
            [year],
        )
        return {"year": year, "cells": [_clean(row) for row in rows], "visual": "heatmap"}

    def late_containment(self, area_name: str, days: int = 90) -> dict:
        """Spills contained slower than the SOP limit. The hours live in the repair reports."""
        limit = self._rule_number("contain_hours", "SR-02", 4)
        start = (self.today - timedelta(days=days)).isoformat()
        rows = self.store.query(
            """
            SELECT s.asset_id, w.name AS well_name, s.inspection_id, s.inspected_at,
                   f.maintenance_id, f.containment_hours
            FROM spills_effective s
            JOIN wells w ON w.asset_id = s.asset_id
            JOIN report_facts f ON f.inspection_id = s.inspection_id
            WHERE s.area_name = ?
              AND s.inspected_at::DATE > ? AND s.inspected_at::DATE <= ?
              AND f.containment_hours > ?
            ORDER BY f.containment_hours DESC, s.asset_id
            """,
            [area_name, start, self.today.isoformat(), limit],
        )
        spills = []
        for row in rows:
            hours = row["containment_hours"]
            spills.append({
                "asset_id": row["asset_id"],
                "well_name": row["well_name"],
                "when": _day(row["inspected_at"]),
                "hours": round(hours, 1),
                "maintenance_id": row["maintenance_id"],
                "inspection_id": row["inspection_id"],
                "detail": (
                    f"Contained after about {hours:.1f} hours, past the {limit:g}-hour limit (SR-02). "
                    f"Report {row['maintenance_id']}, inspection {row['inspection_id']}."
                ),
            })
        summary = (
            f"{len(spills)} spill{'s' if len(spills) != 1 else ''} in {area_name} in the last {days} days "
            f"were not contained within {limit:g} hours. The limit is SR-02, and the time is read from the repair reports."
            if spills else
            f"Every spill in {area_name} in the last {days} days was contained within the {limit:g}-hour SR-02 limit."
        )
        return {
            "area": area_name,
            "days": days,
            "limit_hours": limit,
            "rule": "SR-02",
            "count": len(spills),
            "spills": spills,
            "summary": summary,
            "evidence": [item["inspection_id"] for item in spills] + [item["maintenance_id"] for item in spills],
        }

    def rules_broken(self, asset_id: str, months: int = 12) -> dict:
        """Rules broken in a recent window, plus work that is still open but not yet a breach."""
        start = self.today - timedelta(days=round(months * 30.44))
        start, end = start.isoformat(), self.today.isoformat()
        limit = self._rule_number
        report_hours = limit("report_hours", "SR-01", 24)
        contain_hours = limit("contain_hours", "SR-02", 4)
        verify_days = int(limit("verify_days", "SR-03", 60))
        well = self.store.query("SELECT well_type, name FROM wells WHERE asset_id = ?", [asset_id])
        if not well:
            return self._unknown_well(asset_id)
        rca_count = int(limit("rca_corrective_count", well[0]["well_type"], 3))
        breaches: list[dict] = []

        reports = self.store.query(
            """
            SELECT event_id, event_date, hours_to_report, status, detail, related_inspection_id, fine_usd
            FROM reg_events
            WHERE asset_id = ? AND protocol_id = 'SR-01' AND event_type = 'spill_report_filed'
              AND event_date > ? AND event_date <= ? AND hours_to_report > ?
            """,
            [asset_id, start, end, report_hours],
        )
        for report in reports:
            notice = self.store.query(
                """
                SELECT event_id, fine_usd, status, event_date FROM reg_events
                WHERE asset_id = ? AND event_type = 'notice_of_violation' AND related_inspection_id = ?
                """,
                [asset_id, report["related_inspection_id"]],
            )
            fine = notice[0]["fine_usd"] if notice else 0
            notice_id = notice[0]["event_id"] if notice else None
            open_notice = bool(notice) and notice[0]["status"] != "closed"
            breaches.append({
                "rule": "SR-01",
                "status": "open" if open_notice else "closed",
                "detail": (
                    f"Reported after {report['hours_to_report']:g} hours ({report['event_id']}), "
                    f"against the {report_hours:g}-hour limit."
                    + (f" Notice {notice_id} fined USD {fine:,.0f} and is still open." if open_notice else "")
                ),
                "evidence": [report["event_id"], notice_id, report["related_inspection_id"]],
            })

        slow = self.store.query(
            """
            SELECT f.maintenance_id, f.inspection_id, f.containment_hours, s.inspected_at, s.spill_est_volume_bbl
            FROM report_facts f
            JOIN spills_effective s ON s.inspection_id = f.inspection_id
            WHERE f.asset_id = ? AND f.containment_hours > ?
              AND s.inspected_at::DATE > ? AND s.inspected_at::DATE <= ?
            ORDER BY s.inspected_at
            """,
            [asset_id, contain_hours, start, end],
        )
        for row in slow:
            breaches.append({
                "rule": "SR-02",
                "status": "closed",
                "detail": (
                    f"Contained after about {row['containment_hours']:g} hours, against the {contain_hours:g}-hour limit. "
                    f"Found in repair report {row['maintenance_id']}."
                ),
                "evidence": [row["maintenance_id"], row["inspection_id"]],
            })

        spills = self.store.query(
            """
            SELECT inspection_id, inspected_at, spill_est_volume_bbl
            FROM spills_effective
            WHERE asset_id = ? AND spill_est_volume_bbl >= 5
              AND inspected_at::DATE > ? AND inspected_at::DATE <= ?
            """,
            [asset_id, start, end],
        )
        for spill in spills:
            found = spill["inspected_at"]
            found_on = found.date() if isinstance(found, datetime) else date.fromisoformat(str(found)[:10])
            due = found_on + timedelta(days=verify_days)
            check = self.store.query(
                """
                SELECT event_id, status FROM reg_events
                WHERE related_inspection_id = ? AND protocol_id = 'SR-03'
                ORDER BY event_date DESC LIMIT 1
                """,
                [spill["inspection_id"]],
            )
            verified = bool(check) and check[0]["status"] == "closed"
            if self.today <= due or verified:
                continue
            breaches.append({
                "rule": "SR-03",
                "status": "open",
                "detail": (
                    f"Cleanup verification was due by {_day(due)} and is still open"
                    + (f" ({check[0]['event_id']})." if check else ".")
                ),
                "evidence": [check[0]["event_id"] if check else None, spill["inspection_id"]],
            })

        notices = self.store.query(
            """
            SELECT event_id, event_date, status, fine_usd FROM reg_events
            WHERE asset_id = ? AND event_type = 'notice_of_violation'
              AND event_date > ? AND event_date <= ?
            """,
            [asset_id, start, end],
        )
        for notice in notices:
            due = notice["event_date"] + timedelta(days=30)
            reply = self.store.query(
                """
                SELECT event_id FROM reg_events
                WHERE asset_id = ? AND event_type = 'nov_response_filed' AND event_date >= ? AND event_date <= ?
                """,
                [asset_id, notice["event_date"].isoformat(), due.isoformat()],
            )
            if reply or self.today <= due:
                continue
            breaches.append({
                "rule": "RC-04",
                "status": "open" if notice["status"] != "closed" else "closed",
                "detail": f"The notice needed a response by {_day(due)}, and none is recorded.",
                "evidence": [notice["event_id"]],
            })

        pending = []
        groups = self.store.query(
            """
            SELECT equipment_kind, count(*) AS jobs,
                   list(maintenance_id ORDER BY started_at) AS ids
            FROM maintenance
            WHERE asset_id = ? AND maintenance_type = 'corrective'
              AND started_at::DATE > ? AND started_at::DATE <= ?
            GROUP BY equipment_kind
            HAVING count(*) >= ?
            """,
            [asset_id, start, end, rca_count],
        )
        for group in groups:
            pending.append({
                "rule": "RCA",
                "status": "pending",
                "detail": (
                    f"A root-cause analysis is due on the {group['equipment_kind'].replace('_', ' ')}: "
                    f"{group['jobs']} corrective repairs in the last {months} months ({', '.join(group['ids'])}). "
                    "The deadline has not made this a breach yet."
                ),
                "evidence": list(group["ids"]),
            })
        notes = self.store.query(
            """
            SELECT note_id, text FROM notes
            WHERE asset_id = ? AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)
              AND lower(text) LIKE '%not yet in the maintenance system%'
            """,
            [asset_id, end, end],
        )
        for note in notes:
            pending.append({
                "rule": "Work order",
                "status": "pending",
                "detail": f"{note['note_id']}: the planned shut-in is not yet recorded in the maintenance system, which the manual requires before work starts.",
                "evidence": [note["note_id"]],
            })

        met = []
        tested = self.store.query(
            """
            SELECT maintenance_id, started_at FROM maintenance
            WHERE asset_id = ? AND issue = 'Annual flowline integrity test'
              AND started_at::DATE > ? AND started_at::DATE <= ?
            ORDER BY started_at DESC LIMIT 1
            """,
            [asset_id, start, end],
        )
        if tested:
            met.append({
                "rule": "RC-02",
                "status": "met",
                "detail": f"The yearly flowline test was done on {_day(tested[0]['started_at'])}, so that rule was met.",
                "evidence": [tested[0]["maintenance_id"]],
            })

        for item in (*breaches, *pending, *met):
            item["evidence"] = [value for value in item["evidence"] if value]
        fine = sum(
            row["fine_usd"] or 0 for row in self.store.query(
                "SELECT fine_usd, status FROM reg_events WHERE asset_id = ? AND event_date > ? AND event_date <= ? AND status <> 'closed'",
                [asset_id, start, end],
            )
        )
        open_rules = [item["rule"] for item in breaches if item["status"] == "open"]
        summary = (
            f"{asset_id} ({well[0]['name']}) broke {len(breaches)} rule{'s' if len(breaches) != 1 else ''} "
            f"between {_day(start)} and {_day(end)}."
            + (f" USD {fine:,.0f} in fines is still open." if fine else "")
            + (f" Still open: {', '.join(open_rules)}." if open_rules else " Nothing is still open.")
        )
        return {
            "asset_id": asset_id,
            "well_name": well[0]["name"],
            "from": start,
            "to": end,
            "breach_count": len(breaches),
            "open_fine_usd": fine,
            "breaches": breaches,
            "pending": pending,
            "met": met,
            "summary": summary,
            "evidence": [value for item in breaches for value in item["evidence"]],
        }

    def _rule_number(self, parameter: str, scope: str, fallback: float) -> float:
        """A rule's current value: the latest override by a person if there is one, else the manual's."""
        override = self.store.query(
            "SELECT numeric_value FROM rule_overrides WHERE parameter = ? AND scope_value = ? ORDER BY created_at DESC, override_id DESC LIMIT 1",
            [parameter, scope],
        )
        if override:
            return override[0]["numeric_value"]
        rows = self.store.query(
            "SELECT numeric_value FROM rules WHERE parameter = ? AND scope_value = ?",
            [parameter, scope],
        )
        return rows[0]["numeric_value"] if rows else fallback

    # ── the regulator's letter about spill clean-ups ─────────────────────────────────────

    def spill_letter_compliance(self) -> dict:
        """Which reportable spills the State Oil & Gas Commission's letter covers, and which of them are not compliant.

        The letter covers every reportable spill in the blocks it names since the date it gives, except spills later
        confirmed not to be crude oil (after corrections those are no longer spills). HSE's note on the letter says a
        spill is compliant only if its clean-up report has the required sentence in "Spill clean-up and waste"; field
        soil readings taken on site do not count. Everything is read from the letter, so a new letter is picked up as it is.
        """
        letters = sorted((DATA_DIR / "fleet" / "sops").glob("regulator-letter-*.md"))
        if not letters:
            return {"error": "No regulator letter is loaded."}
        text = letters[-1].read_text(encoding="utf-8")

        def day(value: str):
            for fmt in ("%d %b %Y", "%d %B %Y"):
                try:
                    return datetime.strptime(" ".join(value.split()), fmt).date()
                except ValueError:
                    pass
            return None

        scope = re.search(r"For every reportable spill in (.+?) since (\d{1,2}\s+\w+\s+\d{4})", text, re.S)
        due = re.search(r"submitted by (\d{1,2}\s+\w+\s+\d{4})", text)
        sentence = re.search(r'contains the sentence\s+"([^"]+)"', text)
        written = re.search(r"\*\*Date:\*\*\s*(\d{1,2}\s+\w+\s+\d{4})", text)
        if not (scope and sentence):
            return {"error": "The letter does not state which spills it covers or what a compliant clean-up report must say."}
        known = [r["name"] for r in self.store.query("SELECT name FROM areas")]
        areas = [a for a in known if a.lower() in scope.group(1).lower()]
        since = day(scope.group(2))
        needed = sentence.group(1)
        results_due = day(due.group(1)) if due else None
        threshold = self._rule_number("reportable_bbl", "SR-01", 5)
        if not areas or not since:
            return {"error": "I could not read the blocks or the start date from the letter."}

        marks = ", ".join("?" for _ in areas)
        spills = self.store.query(
            f"""
            SELECT inspection_id, asset_id, well_name, area_name, inspected_at, spill_est_volume_bbl
            FROM spills_effective
            WHERE area_name IN ({marks}) AND inspected_at::DATE >= ? AND spill_est_volume_bbl >= ?
            ORDER BY inspected_at
            """,
            [*areas, since.isoformat(), threshold],
        )
        rows, evidence = [], []
        for s in spills:
            reports = self.store.query(
                "SELECT maintenance_id, report_file FROM maintenance WHERE triggered_by_inspection_id = ? ORDER BY started_at",
                [s["inspection_id"]],
            )
            compliant_with, why, shown = None, "No clean-up report is on file for this spill", "none"
            for rep in reports:
                shown = rep["maintenance_id"]
                body = ""
                if rep["report_file"]:
                    try:
                        body = (DATA_DIR / "assets" / s["asset_id"] / rep["report_file"]).read_text(encoding="utf-8")
                    except OSError:
                        body = ""
                section = re.search(r"##\s*Spill clean-up and waste\s*\n(.*?)(?=\n##|\Z)", body, re.S)
                if section and needed.lower() in section.group(1).lower():
                    compliant_with, why = rep["maintenance_id"], f'The clean-up report says "{needed}"'
                    break
                if section:
                    why = f'The clean-up report ({rep["maintenance_id"]}) has no line "{needed}"' + (" (only field soil readings)" if "sample" in section.group(1).lower() else "")
                else:
                    why = f"The report {rep['maintenance_id']} has no spill clean-up section"
            ok = compliant_with is not None
            rows.append({
                "Well": s["asset_id"], "Area": s["area_name"], "Spill date": f"{s['inspected_at']:%d %b %Y}",
                "Barrels": round(s["spill_est_volume_bbl"], 1), "Clean-up report": compliant_with or shown,
                "Status": "Compliant" if ok else "Not compliant", "Why": why,
            })
            evidence += [s["inspection_id"], *([compliant_with or shown] if (compliant_with or shown) != "none" else [])]
        rows.sort(key=lambda r: (r["Status"] == "Compliant", r["Spill date"] and datetime.strptime(r["Spill date"], "%d %b %Y"), r["Well"]))
        bad = [r for r in rows if r["Status"] != "Compliant"]
        names = areas[0] if len(areas) == 1 else ", ".join(areas[:-1]) + " and " + areas[-1]
        listed = "; ".join(f"{r['Well']} ({r['Spill date']}, {r['Barrels']} bbl)" for r in bad[:12]) + (f"; and {len(bad) - 12} more" if len(bad) > 12 else "")
        left = ""
        if results_due:
            gap = (results_due - self.today).days
            left = f" Results are due by {results_due:%d %b %Y}" + (", which is today." if gap == 0 else f", {gap} day{'s' if gap != 1 else ''} away." if gap > 0 else f", {-gap} day{'s' if gap != -1 else ''} ago.")
        summary = (
            f"The Commission's letter of {day(written.group(1)):%d %b %Y} covers every reportable spill ({_n_(threshold)} bbl or more) in {names} since {since:%d %b %Y}, "
            f"except spills later confirmed not to be crude oil: {len(rows)} spills. {len(rows) - len(bad)} are compliant, because their clean-up report says \"{needed}\". "
            + (f"{len(bad)} are not: {listed}." if bad else "None is non-compliant.")
            + left + " Field soil readings taken on site do not count."
        )
        return {
            "letter": {"file": letters[-1].name, "areas": areas, "since": since.isoformat(), "results_due": results_due.isoformat() if results_due else None, "required_sentence": needed},
            "covered": len(rows), "compliant": len(rows) - len(bad), "not_compliant": len(bad),
            "rows": rows, "summary": summary, "evidence": list(dict.fromkeys(evidence))[:60],
        }

    # ── rules from the manuals and SOPs, with any user override applied ──────────────────

    def effective_rules(self) -> list[dict]:
        """Every extracted rule with its current value: the latest override wins over the manual."""
        rows = self.store.query(
            """
            SELECT r.rule_id, r.source_doc, r.scope_type, r.scope_value, r.parameter, r.unit, r.citation,
                   r.numeric_value AS manual_value,
                   o.numeric_value AS override_value,
                   o.author_user_id AS override_by,
                   o.reason AS override_reason,
                   COALESCE(o.numeric_value, r.numeric_value) AS value
            FROM rules r
            LEFT JOIN (
                SELECT * EXCLUDE (rn) FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY scope_type, scope_value, parameter
                        ORDER BY created_at DESC, override_id DESC
                    ) AS rn
                    FROM rule_overrides
                ) WHERE rn = 1
            ) o ON o.scope_type = r.scope_type AND o.scope_value = r.scope_value AND o.parameter = r.parameter
            ORDER BY r.scope_type, r.scope_value, r.parameter
            """
        )
        return [_clean(row) for row in rows]

    def well_type_rules(self, well_type: str | None = None, topic: str | None = None) -> dict:
        """What each well type needs to count as producing, its temperature alarm, and the spill deadlines.

        `topic` narrows it to "temperature" or "spill". The numbers come from the manuals as loaded
        at ingest, so a new well type or a corrected value is picked up without a code change.
        """
        rules = self.effective_rules()
        types: dict[str, dict] = {}
        for rule in rules:
            if rule["scope_type"] != "well_type":
                continue
            entry = types.setdefault(rule["scope_value"], {"well_type": rule["scope_value"]})
            entry[rule["parameter"]] = rule["value"]
            entry.setdefault("rule_ids", {})[rule["parameter"]] = rule["rule_id"]
            if rule["override_value"] is not None:
                entry.setdefault("overridden", {})[rule["parameter"]] = {
                    "manual_value": rule["manual_value"], "by": rule["override_by"], "reason": rule["override_reason"],
                }
        known = sorted(types)
        if well_type:
            wanted = well_type.upper()
            if wanted not in types:
                return {"error": f"No manual is loaded for well type {wanted}.", "known_types": known}
            types = {wanted: types[wanted]}
        sops = [
            {"sop": r["scope_value"], "parameter": r["parameter"], "value": r["value"], "unit": r["unit"],
             "citation": r["citation"], "rule_id": r["rule_id"]}
            for r in rules if r["scope_type"] == "sop"
        ]
        if topic == "spill":
            types = {}
        elif topic == "temperature":
            sops = []
        shown = [types[t] for t in sorted(types)]
        evidence = [f"RULE-{rid}" for t in shown for rid in t.get("rule_ids", {}).values()] + [f"RULE-{s['rule_id']}" for s in sops]
        return {"types": shown, "sops": sops, "known_types": known, "topic": topic, "evidence": evidence}

    def evaluate_reading(
        self,
        ambient_c: float,
        wellhead_c: float,
        well_type: str | None = None,
        asked_types: list[str] | None = None,
    ) -> dict:
        """A made-up reading (no real well): the gap, and the verdict under every well type's own rule."""
        gap = round(float(wellhead_c) - float(ambient_c), 2)
        known = self.well_type_rules()["types"]
        rows = []
        for entry in known:
            need, alarm = entry.get("producing_delta_c"), entry.get("temp_alarm_c")
            rows.append({
                "well_type": entry["well_type"],
                "required_gap_c": need,
                "producing_on_reading": None if need is None else gap >= need,
                "shortfall_c": None if need is None else round(max(0.0, need - gap), 2),
                "alarm_limit_c": alarm,
                "above_alarm": None if alarm is None else float(wellhead_c) > alarm,
            })
        evidence = [
            f"RULE-{rid}" for entry in known
            for param, rid in entry.get("rule_ids", {}).items() if param in ("producing_delta_c", "temp_alarm_c")
        ]
        return {
            "hypothetical": True,
            "reading": {"ambient_c": float(ambient_c), "wellhead_c": float(wellhead_c), "gap_c": gap},
            "well_type": well_type.upper() if well_type else None,
            "asked_types": [t.upper() for t in (asked_types or [])],
            "types": rows,
            "rule": "A well is producing only when the wellhead is at least its type's required gap above the air.",
            "evidence": evidence,
        }

    # ── free-form questions: one read-only SELECT over an allow-list ─────────────────────

    QUERYABLE = (
        "wells", "areas", "teams", "users", "equipment", "inspections", "inspection_status", "well_now",
        "spills_effective", "maintenance", "reg_events", "notes", "corrections_latest", "rules",
    )

    def schema_text(self) -> str:
        """Tables and columns the SQL writer may use. Text columns with few distinct values list them, so a
        filter is written against what the data really contains instead of a guess ('repair' vs 'corrective')."""
        cached = getattr(self, "_schema_cache", None)
        if cached:
            return cached
        rows = self.store.query(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            f"WHERE table_name IN ({', '.join('?' * len(self.QUERYABLE))}) ORDER BY table_name, ordinal_position",
            list(self.QUERYABLE),
        )
        tables: dict[str, list[str]] = {}
        for r in rows:
            label = f"{r['column_name']} {r['data_type']}"
            if r["data_type"] == "VARCHAR" and not r["column_name"].endswith(("_id", "text", "detail", "notes", "reason", "issue", "action_taken", "name")):
                values = [
                    v["v"] for v in self.store.query(
                        f"SELECT DISTINCT {r['column_name']} AS v FROM {r['table_name']} WHERE {r['column_name']} IS NOT NULL LIMIT 13"
                    )
                ]
                if 0 < len(values) <= 12 and all(len(str(v)) <= 32 for v in values):
                    label += " one of [" + ", ".join(f"'{v}'" for v in sorted(values)) + "]"
            tables.setdefault(r["table_name"], []).append(label)
        self._schema_cache = "\n".join(f"{name}({', '.join(cols)})" for name, cols in tables.items())
        return self._schema_cache

    _FORBIDDEN = re.compile(
        r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|pragma|install|load|export|import|call|set|"
        r"vacuum|checkpoint|truncate|replace|merge|grant|read_csv|read_json|read_parquet|read_text|glob|httpfs)\b",
        re.IGNORECASE,
    )

    def run_select(self, sql: str, limit: int = 200) -> dict:
        """Run one SELECT the model wrote. Only the allow-listed tables, one statement, never a write."""
        text = sql.strip().rstrip(";").strip()
        if ";" in text:
            return {"sql": sql, "error": "Only one statement is allowed."}
        if not re.match(r"(?is)^\s*(select|with)\b", text):
            return {"sql": sql, "error": "Only SELECT queries are allowed."}
        if self._FORBIDDEN.search(text):
            return {"sql": sql, "error": "That statement uses something that is not allowed."}
        ctes = {m.lower() for m in re.findall(r"(?i)\b(\w+)\s+as\s*\(", text)}
        used = re.findall(r"(?i)\b(?:from|join)\s+([A-Za-z_][\w\.]*)", text)
        bad = sorted({u for u in used if u.lower() not in ctes and u.lower().split(".")[-1] not in self.QUERYABLE})
        if bad:
            return {"sql": sql, "error": f"Unknown table: {', '.join(bad)}. Use only: {', '.join(self.QUERYABLE)}."}
        try:
            rows = self.store.query(f"SELECT * FROM ({text}) AS q LIMIT {limit + 1}")
        except Exception as exc:  # the message goes back to the model so it can fix the query
            return {"sql": text, "error": str(exc).splitlines()[0][:300]}
        truncated = len(rows) > limit
        rows = [_clean(r) for r in rows[:limit]]
        return {"sql": text, "columns": list(rows[0].keys()) if rows else [], "rows": rows, "row_count": len(rows), "truncated": truncated, "evidence": []}

    def flowline_bypasses(self) -> dict:
        """Temporary flowline bypasses, from operator notes, judged against the MOC memos (OPS-2)."""
        approvals = _bypass_approvals(DATA_DIR / "fleet" / "sops")
        areas = {
            row["asset_id"]: row["area"]
            for row in self.store.query(
                "SELECT w.asset_id, a.name AS area FROM wells w JOIN areas a ON a.area_id = w.area_id"
            )
        }
        notes: dict[str, list[dict]] = {}
        for path in sorted((DATA_DIR / "assets").glob("*/context.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("note_type") != "operator_note" or "bypass" not in row.get("text", "").lower():
                    continue
                notes.setdefault(row["asset_id"], []).append(row)

        rows = []
        for asset_id, items in notes.items():
            items.sort(key=lambda item: item["created_at"])
            opened = None
            for item in items:
                text = item["text"]
                if _BYPASS_OUT.search(text) and opened:
                    rows.append(_bypass_row(asset_id, areas.get(asset_id, ""), opened, item, approvals, self.today))
                    opened = None
                elif _BYPASS_IN.search(text) and opened is None:
                    opened = item
            if opened:
                rows.append(_bypass_row(asset_id, areas.get(asset_id, ""), opened, None, approvals, self.today))

        shown = [row for row in rows if row["Bypass out"] == "still in" or row["Status"] != "Allowed"]
        shown.sort(key=lambda row: (row["Bypass out"] != "still in", row["Well"]))
        still = [row for row in shown if row["Bypass out"] == "still in"]
        allowed = [row for row in still if row["Status"] == "Allowed"]
        summary = (
            f"{len(still)} temporary flowline bypass"
            f"{' is' if len(still) == 1 else 'es are'} in place today"
            f"{f', {len(allowed)} of them allowed' if still else ''}. "
            f"{len(shown)} bypasses are listed: the ones in place now, and any past or present bypass "
            f"that stayed in after its approval ran out or was never approved."
        )
        return {
            "count": len(shown),
            "in_place": len(still),
            "rows": shown,
            "summary": summary,
            "evidence": [item for row in shown for item in row["evidence"]],
        }

    def fleet_summary(self) -> dict:
        """How many wells, by type and area, and how many are producing right now."""
        total = self.store.query("SELECT count(*) AS n FROM wells")[0]["n"]
        by_type = {row["well_type"]: row["n"] for row in self.store.query(
            "SELECT well_type, count(*) AS n FROM wells GROUP BY well_type ORDER BY well_type"
        )}
        by_area = {row["name"]: row["n"] for row in self.store.query(
            "SELECT a.name, count(*) AS n FROM wells w JOIN areas a ON a.area_id = w.area_id GROUP BY a.name ORDER BY a.name"
        )}
        now = self.store.query(
            "SELECT count(*) FILTER (WHERE producing) AS producing, count(*) FILTER (WHERE NOT producing) AS stopped FROM well_now"
        )[0]
        return {
            "total_wells": total,
            "by_type": by_type,
            "by_area": by_area,
            "producing_now": now["producing"],
            "not_producing_now": now["stopped"],
            "rows": [{"well_type": name, "wells": count} for name, count in by_type.items()],
        }

    def repair_kinds(self) -> dict:
        """Every distinct repair, and the equipment it was done on, across the whole field."""
        rows = self.store.query(
            """
            SELECT replace(equipment_kind, '_', ' ') AS equipment,
                   issue AS repair,
                   maintenance_type AS kind,
                   count(*) AS jobs,
                   count(DISTINCT asset_id) AS wells
            FROM maintenance
            GROUP BY equipment_kind, issue, maintenance_type
            ORDER BY equipment_kind, jobs DESC, issue
            """
        )
        rows = [_clean(row) for row in rows]
        equipment = sorted({row["equipment"] for row in rows})
        lines = []
        for name in equipment:
            jobs = [row for row in rows if row["equipment"] == name]
            bits = [f"{row['repair']} ({row['kind']}, {row['jobs']} jobs)" for row in jobs]
            lines.append(f"{name}: " + "; ".join(bits) + ".")
        summary = (
            f"{len(rows)} kinds of repair have been done across all wells, on {len(equipment)} kinds of equipment. "
            + " ".join(lines)
        )
        return {
            "count": len(rows),
            "equipment_count": len(equipment),
            "rows": rows,
            "summary": summary,
        }

    def repair_cost(self, asset_ids: list[str], year: int) -> dict:
        """What maintenance (scheduled and corrective) cost each of these wells in a calendar year, in the order asked."""
        ids = list(dict.fromkeys(str(a).upper() for a in asset_ids))
        if not ids:
            return {"year": year, "wells": [], "total_usd": 0, "evidence": []}
        marks = ", ".join("?" for _ in ids)
        rows = self.store.query(
            f"""
            SELECT w.asset_id, w.name AS well_name, count(m.maintenance_id) AS jobs,
                   coalesce(round(sum(m.total_usd), 0), 0) AS cost_usd
            FROM wells w
            LEFT JOIN maintenance m ON m.asset_id = w.asset_id AND year(m.started_at) = ?
            WHERE w.asset_id IN ({marks})
            GROUP BY w.asset_id, w.name
            """,
            [year, *ids],
        )
        wells = sorted((_clean(r) for r in rows), key=lambda r: ids.index(r["asset_id"]))
        records = self.store.query(
            f"SELECT maintenance_id FROM maintenance WHERE asset_id IN ({marks}) AND year(started_at) = ? ORDER BY started_at",
            [*ids, year],
        )
        return {
            "year": year,
            "wells": wells,
            "total_usd": sum(w["cost_usd"] for w in wells),
            "scope": "all maintenance jobs started that year, scheduled and corrective",
            "evidence": [r["maintenance_id"] for r in records][:40],
        }

    def record_correction(
        self,
        *,
        asset_id: str,
        inspection_id: str,
        field: str,
        new_value: str,
        author_user_id: str,
        reason: str,
    ) -> dict:
        if field not in {"wellhead_temp_c", "spill_detected", "spill_est_volume_bbl", "ambient_temp_c"}:
            return {"error": f"{field} cannot be corrected"}
        original = self.store.query(
            f"SELECT {field} AS original FROM inspections WHERE inspection_id = ?",
            [inspection_id],
        )
        if not original:
            return {"error": f"{inspection_id} was not found"}
        correction_id = f"CORR-RT-{inspection_id}-{field}"
        self.store.execute("DELETE FROM corrections WHERE correction_id = ?", [correction_id])
        self.store.execute(
            """
            INSERT INTO corrections VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'record', current_timestamp, 'runtime')
            """,
            [correction_id, asset_id, inspection_id, field, str(original[0]["original"]), new_value, author_user_id, reason],
        )
        return {"correction_id": correction_id, "original": original[0]["original"], "new_value": new_value, "field": field}
