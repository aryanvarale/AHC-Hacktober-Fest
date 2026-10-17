"""Load the field files into DuckDB once, then build the views every answer uses."""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb

from aicopilot.extract import extract_report_facts, extract_rules

_VIEWS = """
CREATE OR REPLACE VIEW corrections_latest AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY inspection_id, field ORDER BY created_at DESC, correction_id DESC
    ) AS rn
    FROM corrections
) WHERE rn = 1;

CREATE OR REPLACE VIEW inspection_status AS
SELECT
    i.inspection_id,
    i.asset_id,
    i.inspected_at,
    i.inspector_user_id,
    i.method,
    i.ambient_temp_c,
    i.wellhead_temp_c AS wellhead_temp_original,
    COALESCE(TRY_CAST(tc.new_value AS DOUBLE), i.wellhead_temp_c) AS wellhead_temp_c,
    i.notes,
    i.spill_detected AS spill_detected_original,
    CASE
        WHEN sc.new_value IS NOT NULL THEN lower(sc.new_value) IN ('true', '1', 'yes')
        ELSE i.spill_detected
    END AS spill_detected,
    i.spill_area_pct,
    i.spill_est_volume_bbl AS spill_volume_original,
    COALESCE(TRY_CAST(vc.new_value AS DOUBLE), i.spill_est_volume_bbl) AS spill_est_volume_bbl,
    w.well_type,
    w.name AS well_name,
    w.area_id,
    a.name AS area_name,
    w.team_id,
    w.recorded_status,
    w.lat,
    w.lon,
    COALESCE(ov.numeric_value, r.numeric_value) AS producing_delta_c,
    COALESCE(TRY_CAST(tc.new_value AS DOUBLE), i.wellhead_temp_c) - i.ambient_temp_c AS delta_c,
    (COALESCE(TRY_CAST(tc.new_value AS DOUBLE), i.wellhead_temp_c) - i.ambient_temp_c)
        >= COALESCE(ov.numeric_value, r.numeric_value) AS producing,
    tc.author_user_id AS temp_corrected_by,
    tc.reason AS temp_correction_reason,
    tc.original_value AS temp_original_value,
    sc.author_user_id AS spill_corrected_by,
    sc.reason AS spill_correction_reason,
    vc.author_user_id AS volume_corrected_by,
    vc.reason AS volume_correction_reason,
    vc.original_value AS volume_original_value
FROM inspections i
JOIN wells w ON w.asset_id = i.asset_id
JOIN areas a ON a.area_id = w.area_id
LEFT JOIN rules r
    ON r.parameter = 'producing_delta_c' AND r.scope_value = w.well_type
LEFT JOIN rule_overrides ov
    ON ov.parameter = 'producing_delta_c'
   AND ov.scope_type = 'well_type'
   AND ov.scope_value = w.well_type
LEFT JOIN corrections_latest tc
    ON tc.inspection_id = i.inspection_id AND tc.field = 'wellhead_temp_c'
LEFT JOIN corrections_latest sc
    ON sc.inspection_id = i.inspection_id AND sc.field = 'spill_detected'
LEFT JOIN corrections_latest vc
    ON vc.inspection_id = i.inspection_id AND vc.field = 'spill_est_volume_bbl';

CREATE OR REPLACE VIEW well_now AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (PARTITION BY asset_id ORDER BY inspected_at DESC) AS rn
    FROM inspection_status
) WHERE rn = 1;

CREATE OR REPLACE VIEW spills_effective AS
SELECT *
FROM inspection_status
WHERE spill_detected
  AND spill_est_volume_bbl IS NOT NULL;
"""


NULL_MARK = "\\N"   # how a missing value is written in the temporary CSV


def _many(con: duckdb.DuckDBPyConnection, sql: str, rows) -> None:
    """Load rows through a temporary CSV and DuckDB's own COPY.

    Passing rows as Python parameters (executemany, or big VALUES lists) makes the DuckDB bridge probe for optional
    modules on every value, which took 96 % of the time and 11 minutes for the test dataset. COPY reads the file natively.
    """
    rows = list(rows)
    if not rows:
        return
    table = re.search(r"INSERT INTO (\w+)", sql).group(1)
    handle, name = tempfile.mkstemp(suffix=".csv")
    os.close(handle)
    try:
        with open(name, "w", newline="", encoding="utf-8") as out:
            writer = csv.writer(out)
            for row in rows:
                writer.writerow([NULL_MARK if v is None else (str(v).lower() if isinstance(v, bool) else v) for v in row])
        path = name.replace("\\", "/").replace("'", "''")
        con.execute(f"COPY {table} FROM '{path}' (FORMAT csv, HEADER false, NULL '{NULL_MARK}')")
    finally:
        os.unlink(name)


def is_complete(db_path: Path) -> bool:
    """True only for a database that was fully built. A file left behind by an interrupted load is not."""
    if not db_path.exists():
        return False
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        return False
    try:
        try:
            return con.execute("SELECT count(*) FROM ingest_meta").fetchone()[0] > 0
        except Exception:
            # built before the marker existed: accept it only if every core table has rows
            return all(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] > 0 for t in ("wells", "inspections", "maintenance", "rules"))
    except Exception:
        return False
    finally:
        con.close()


def ingest(data_dir: Path, db_path: Path) -> dict:
    """Build the database from the data folder.

    It is built in a separate file and moved over the real one only when every table is loaded, so an
    interruption (a restart, a killed process) can never leave a half-loaded database behind.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    building = db_path.with_name(db_path.name + ".building")
    for leftover in (building, Path(str(building) + ".wal")):
        leftover.unlink(missing_ok=True)
    con = duckdb.connect(str(building))
    try:
        _create_tables(con)
        _load_fleet(con, data_dir / "fleet")
        _load_wells(con, data_dir / "assets")
        _load_jsonl(con, data_dir / "assets")
        _seed_corrections(con)
        _load_rules(con, data_dir / "fleet")
        _load_report_facts(con, data_dir / "assets")
        con.execute(_VIEWS)
        counts = {
            name: con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in (
                "wells", "inspections", "maintenance", "reg_events", "notes",
                "corrections", "rules", "report_facts",
            )
        }
        con.execute("CREATE TABLE ingest_meta (finished_at TIMESTAMP, data_dir VARCHAR, wells BIGINT, inspections BIGINT)")
        con.execute("INSERT INTO ingest_meta VALUES (?, ?, ?, ?)", [datetime.now(), str(data_dir), counts["wells"], counts["inspections"]])
    except BaseException:
        con.close()
        building.unlink(missing_ok=True)
        Path(str(building) + ".wal").unlink(missing_ok=True)
        raise
    con.close()
    Path(str(db_path) + ".wal").unlink(missing_ok=True)   # a stale log from the file being replaced
    os.replace(building, db_path)
    return counts


def _create_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE areas (area_id VARCHAR PRIMARY KEY, name VARCHAR, team_id VARCHAR, lat DOUBLE, lon DOUBLE);
        CREATE TABLE teams (team_id VARCHAR PRIMARY KEY, name VARCHAR, lead_user_id VARCHAR);
        CREATE TABLE users (user_id VARCHAR PRIMARY KEY, name VARCHAR, role VARCHAR, team_id VARCHAR);
        CREATE TABLE wells (
            asset_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            well_type VARCHAR,
            area_id VARCHAR,
            team_id VARCHAR,
            in_charge_user_id VARCHAR,
            lat DOUBLE,
            lon DOUBLE,
            installed_on DATE,
            recorded_status VARCHAR,
            last_inspection_id VARCHAR,
            last_inspected_at TIMESTAMP,
            last_inspector_user_id VARCHAR
        );
        CREATE TABLE equipment (
            equipment_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            kind VARCHAR,
            model VARCHAR
        );
        CREATE TABLE inspections (
            inspection_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            inspected_at TIMESTAMP,
            inspector_user_id VARCHAR,
            method VARCHAR,
            ambient_temp_c DOUBLE,
            wellhead_temp_c DOUBLE,
            spill_detected BOOLEAN,
            spill_area_pct DOUBLE,
            spill_est_volume_bbl DOUBLE,
            notes VARCHAR
        );
        CREATE TABLE maintenance (
            maintenance_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            maintenance_type VARCHAR,
            equipment_kind VARCHAR,
            issue VARCHAR,
            action_taken VARCHAR,
            technician_user_id VARCHAR,
            contractor VARCHAR,
            downtime_hours DOUBLE,
            labor_usd DOUBLE,
            parts_usd DOUBLE,
            total_usd DOUBLE,
            triggered_by_inspection_id VARCHAR,
            report_file VARCHAR
        );
        CREATE TABLE reg_events (
            event_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            event_date DATE,
            agency VARCHAR,
            event_type VARCHAR,
            protocol_id VARCHAR,
            related_inspection_id VARCHAR,
            detail VARCHAR,
            hours_to_report DOUBLE,
            fine_usd DOUBLE,
            status VARCHAR
        );
        CREATE TABLE notes (
            note_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            created_at TIMESTAMP,
            author_user_id VARCHAR,
            note_type VARCHAR,
            inspection_id VARCHAR,
            text VARCHAR,
            valid_from DATE,
            valid_to DATE
        );
        CREATE TABLE corrections (
            correction_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            inspection_id VARCHAR,
            field VARCHAR,
            original_value VARCHAR,
            new_value VARCHAR,
            author_user_id VARCHAR,
            reason VARCHAR,
            scope VARCHAR,
            created_at TIMESTAMP,
            source VARCHAR
        );
        CREATE TABLE rules (
            rule_id VARCHAR PRIMARY KEY,
            source_doc VARCHAR,
            scope_type VARCHAR,
            scope_value VARCHAR,
            parameter VARCHAR,
            numeric_value DOUBLE,
            unit VARCHAR,
            citation VARCHAR
        );
        CREATE TABLE rule_overrides (
            override_id VARCHAR PRIMARY KEY,
            scope_type VARCHAR,
            scope_value VARCHAR,
            parameter VARCHAR,
            numeric_value DOUBLE,
            author_user_id VARCHAR,
            reason VARCHAR,
            created_at TIMESTAMP
        );
        CREATE TABLE report_facts (
            maintenance_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            inspection_id VARCHAR,
            containment_hours DOUBLE,
            report_file VARCHAR
        );
        CREATE TABLE well_claims (
            claim_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR,
            author_user_id VARCHAR,
            text VARCHAR,
            status VARCHAR,
            created_at TIMESTAMP
        );
        CREATE TABLE threads (
            thread_id VARCHAR PRIMARY KEY,
            user_id VARCHAR,
            title VARCHAR,
            created_at TIMESTAMP
        );
        CREATE TABLE messages (
            message_id VARCHAR PRIMARY KEY,
            thread_id VARCHAR,
            user_id VARCHAR,
            role VARCHAR,
            content VARCHAR,
            created_at TIMESTAMP
        );
        CREATE TABLE discussed_entities (
            id VARCHAR PRIMARY KEY,
            user_id VARCHAR,
            thread_id VARCHAR,
            asset_id VARCHAR,
            discussed_on DATE
        );
    """)


def _load_fleet(con: duckdb.DuckDBPyConnection, fleet: Path) -> None:
    areas = json.loads((fleet / "areas.json").read_text(encoding="utf-8"))
    _many(con, 
        "INSERT INTO areas VALUES (?, ?, ?, ?, ?)",
        [(a["area_id"], a["name"], a["team_id"], a["center"]["lat"], a["center"]["lon"]) for a in areas["areas"]],
    )
    _many(con, 
        "INSERT INTO teams VALUES (?, ?, ?)",
        [(t["team_id"], t["name"], t["lead_user_id"]) for t in areas["teams"]],
    )
    users = json.loads((fleet / "users.json").read_text(encoding="utf-8"))
    _many(con, 
        "INSERT INTO users VALUES (?, ?, ?, ?)",
        [(u["user_id"], u["name"], u["role"], u["team_id"]) for u in users["users"]],
    )


def _load_wells(con: duckdb.DuckDBPyConnection, assets: Path) -> None:
    wells = []
    equipment = []
    for path in sorted(assets.glob("*/metadata.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        loc = meta["location"]
        last = meta.get("last_inspection") or {}
        wells.append((
            meta["asset_id"], meta["name"], meta["well_type"], meta["area_id"], meta["team_id"],
            meta["in_charge_user_id"], loc["lat"], loc["lon"], meta["installed_on"],
            meta["recorded_status"], last.get("inspection_id"), last.get("inspected_at"),
            last.get("inspector_user_id"),
        ))
        for item in meta.get("equipment", []):
            equipment.append((item["equipment_id"], meta["asset_id"], item["kind"], item["model"]))
    _many(con, "INSERT INTO wells VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", wells)
    _many(con, "INSERT INTO equipment VALUES (?, ?, ?, ?)", equipment)


def _load_jsonl(con: duckdb.DuckDBPyConnection, assets: Path) -> None:
    inspections = []
    maintenance = []
    events = []
    notes = []
    corrections = []
    for path in sorted(assets.glob("*/inspections.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            inspections.append((
                row["inspection_id"], row["asset_id"], row["inspected_at"], row.get("inspector_user_id"),
                row.get("method"), row.get("ambient_temp_c"), row.get("wellhead_temp_c"),
                bool(row.get("spill_detected")), row.get("spill_area_pct"), row.get("spill_est_volume_bbl"),
                row.get("notes") or "",
            ))
    for path in sorted(assets.glob("*/maintenance.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            cost = row.get("cost_usd") or {}
            maintenance.append((
                row["maintenance_id"], row["asset_id"], row["started_at"], row.get("completed_at"),
                row.get("maintenance_type"), row.get("equipment_kind"), row.get("issue"),
                row.get("action_taken"), row.get("technician_user_id"), row.get("contractor"),
                row.get("downtime_hours"), cost.get("labor"), cost.get("parts"), cost.get("total"),
                row.get("triggered_by_inspection_id"), row.get("report_file"),
            ))
    for path in sorted(assets.glob("*/regulatory-events.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            events.append((
                row["event_id"], row["asset_id"], row["date"], row.get("agency"), row.get("event_type"),
                row.get("protocol_id"), row.get("related_inspection_id"), row.get("detail"),
                row.get("hours_to_report"), row.get("fine_usd"), row.get("status"),
            ))
    for path in sorted(assets.glob("*/context.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            notes.append((
                row["note_id"], row["asset_id"], row["created_at"], row.get("author_user_id"),
                row.get("note_type"), row.get("inspection_id"), row.get("text"),
                row.get("valid_from"), row.get("valid_to"),
            ))
            if row.get("note_type") == "data_correction" and row.get("inspection_id") and row.get("field"):
                corrections.append((
                    f"CORR-{row['note_id']}", row["asset_id"], row["inspection_id"], row["field"],
                    str(row.get("original_value")), str(row.get("corrected_value")),
                    row.get("author_user_id"), row.get("text") or "", "record", row["created_at"], "dataset",
                ))
    _many(con, "INSERT INTO inspections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", inspections)
    _many(con, "INSERT INTO maintenance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", maintenance)
    _many(con, "INSERT INTO reg_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", events)
    _many(con, "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", notes)
    if corrections:
        _many(con, "INSERT INTO corrections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", corrections)


def _seed_corrections(con: duckdb.DuckDBPyConnection) -> None:
    """A dataset correction that says a detection was not crude overrides the spill flag.
    The original inspection row is left unchanged."""
    rows = con.execute("""
        SELECT n.note_id, n.asset_id, n.inspection_id, i.spill_detected, n.author_user_id, n.text, n.created_at
        FROM notes n
        JOIN inspections i ON i.inspection_id = n.inspection_id
        WHERE n.note_type = 'correction'
          AND n.inspection_id IS NOT NULL
    """).fetchall()
    seeded = []
    for note_id, asset_id, inspection_id, original, author, text, created_at in rows:
        if "not a" in text.lower() and "spill" in text.lower():
            seeded.append((
                f"CORR-{note_id}", asset_id, inspection_id, "spill_detected",
                str(bool(original)).lower(), "false", author, text, "record", created_at, "dataset",
            ))
    if seeded:
        _many(con, "INSERT INTO corrections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", seeded)


def _load_rules(con: duckdb.DuckDBPyConnection, fleet: Path) -> None:
    rules = extract_rules(fleet)
    _many(con, 
        "INSERT INTO rules VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(r["rule_id"], r["source_doc"], r["scope_type"], r["scope_value"], r["parameter"],
          r["numeric_value"], r["unit"], r["citation"]) for r in rules],
    )


def _load_report_facts(con: duckdb.DuckDBPyConnection, assets: Path) -> None:
    facts = extract_report_facts(assets)
    if not facts:
        return
    _many(con, 
        "INSERT INTO report_facts VALUES (?, ?, ?, ?, ?)",
        [(f["maintenance_id"], f["asset_id"], f["inspection_id"], f["containment_hours"], f["report_file"])
         for f in facts],
    )
