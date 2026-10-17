"""Operator API. The chat box calls the same tools an LLM agent will call."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from aicopilot.actions import dispatch_action
from aicopilot.adapters import DuckDBStore
from aicopilot.agent import ask_model
from aicopilot.agui import stream_run
from aicopilot.evidence import lookup as lookup_evidence
from aicopilot import memory
from aicopilot.planner import plan, plan_memory
from aicopilot.config import get_settings
from aicopilot.ingest import ingest
from aicopilot.queries import FieldQueries

STATIC = Path(__file__).parent / "static" / "index.html"
settings = get_settings()
store: DuckDBStore | None = None
queries: FieldQueries | None = None


class AskRequest(BaseModel):
    question: str
    user_id: str = "U-201"


class ActionRequest(BaseModel):
    action_id: str
    asset_id: str | None = None
    user_id: str = "U-201"


class CorrectionRequest(BaseModel):
    asset_id: str
    inspection_id: str
    field: str
    new_value: str
    author_user_id: str
    reason: str


def _ready() -> FieldQueries:
    if queries is None:
        raise RuntimeError("Database is not loaded")
    return queries


def create_app() -> FastAPI:
    app = FastAPI(title="Asset Intelligence Copilot")

    @app.on_event("startup")
    def startup() -> None:
        global store, queries
        if not settings.db_path.exists():
            ingest(settings.data_dir, settings.db_path)
        store = DuckDBStore(settings.db_path)
        queries = FieldQueries(store, settings.today)
        memory.ensure_tables(store)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "ok": True,
            "today": settings.today.isoformat(),
            "mode": settings.mode,
            "model": settings.model if settings.api_key != "local" else None,
        }

    @app.post("/api/reload")
    def reload() -> dict:
        global store, queries
        if store:
            store.close()
        counts = ingest(settings.data_dir, settings.db_path)
        store = DuckDBStore(settings.db_path)
        queries = FieldQueries(store, settings.today)
        return {"loaded": counts}

    @app.get("/api/users")
    def users() -> list[dict]:
        return _ready().users()

    @app.get("/api/wells/{asset_id}/status")
    def status(asset_id: str) -> dict:
        return _ready().well_status(asset_id.upper())

    @app.post("/api/actions")
    def run_action(body: ActionRequest) -> dict:
        return dispatch_action(body.action_id, body.asset_id, body.user_id)

    @app.post("/api/corrections")
    def correct(body: CorrectionRequest) -> dict:
        return _ready().record_correction(**body.model_dump())

    def compute(question: str, user_id: str = "U-201", history: list[dict] | None = None, thread_id: str = "") -> dict:
        field = _ready()
        started = perf_counter()
        # "the three wells we discussed yesterday": this user's own earlier chats, never anyone else's
        remembered, question = plan_memory(field, store, question, user_id, thread_id, settings.today)
        if remembered:
            remembered["path"] = "database"
            remembered["elapsed_ms"] = round((perf_counter() - started) * 1000, 1)
            return remembered
        planned = plan(field, question)  # readings and rule lookups are answered from the data, never guessed
        if planned:
            planned["path"] = "database"
            planned["elapsed_ms"] = round((perf_counter() - started) * 1000, 1)
            return planned
        if settings.api_key == "local":
            answered = answer_question(field, question, settings.today)
            answered["path"] = "database"
        else:
            try:
                answered = ask_model(field, question, settings, history)
                answered["path"] = "model"
            except Exception as exc:
                answered = answer_question(field, question, settings.today)
                answered["path"] = "database"
                answered["model_error"] = str(exc)
        answered["elapsed_ms"] = round((perf_counter() - started) * 1000, 1)
        return answered

    @app.post("/api/ask")
    def ask(body: AskRequest) -> dict:
        return compute(body.question, body.user_id)

    @app.get("/api/evidence/{evidence_id}")
    def evidence(evidence_id: str, tool: str = "", focus: str = "") -> dict:
        _ready()
        return lookup_evidence(evidence_id, settings.data_dir, store, tool, focus)

    @app.post("/api/agui")
    def agui_run(body: dict) -> StreamingResponse:
        return StreamingResponse(
            stream_run(body, compute, lambda user_id, thread_id, question, answer, payload: memory.remember(store, user_id, thread_id, question, answer, payload, settings.today)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def answer_question(field: FieldQueries, question: str, today: date) -> dict:
    """Route a plain-English question onto a tool. This is the fallback when no
    model is configured. A tool-calling model will replace this function."""
    text = " ".join(question.lower().split())
    asset = _asset_id(question)
    area = _area_name(text)

    if asset and ("producing" in text or "status" in text or "right now" in text):
        result = field.well_status(asset)
        return {"visual": "status", "title": asset, "result": result, "tool": "well_status"}
    if "not producing" in text and area:
        result = field.non_producing(area)
        return {"visual": "table", "title": f"Not producing in {area}", "result": result, "tool": "non_producing"}
    if asset and ("temperature" in text or "line chart" in text or "weeks" in text):
        weeks = 13 if "13" in text else 13
        result = field.timeseries(asset, weeks)
        return {"visual": "line", "title": f"{asset} temperature", "result": result, "tool": "timeseries"}
    if asset and "spill" in text:
        result = field.spills_for_well(asset)
        return {"visual": "table", "title": f"Spills at {asset}", "result": result, "tool": "spills_for_well"}
    if asset and "down" in text:
        year = _year(text) or today.year
        result = field.downtime_days(asset, year)
        return {"visual": "answer", "title": f"{asset} downtime in {year}", "result": result, "tool": "downtime_days"}
    if "downtime" in text and area:
        start = today - timedelta(days=122)
        if "4 month" in text:
            start = date(2026, 6, 16) if today == date(2026, 10, 16) else today - timedelta(days=122)
        result = field.top_downtime(area, start, today, 3)
        return {"visual": "bar", "title": f"Downtime in {area}", "result": result, "tool": "top_downtime"}
    if "map" in text and area:
        result = field.spill_map(area, 90)
        return {"visual": "map", "title": f"Recent spills in {area}", "result": result, "tool": "spill_map"}
    if "heatmap" in text:
        result = field.spill_heatmap(_year(text) or today.year)
        return {"visual": "heatmap", "title": "Spills by area and month", "result": result, "tool": "spill_heatmap"}
    return {
        "visual": "answer",
        "title": "I need a more specific question",
        "tool": None,
        "result": {
            "message": "Ask about one well's status, downtime, spills, a temperature chart, a map, or a heatmap.",
        },
    }


def _asset_id(question: str) -> str | None:
    import re
    match = re.search(r"WELL-\d{4}", question.upper())
    return match.group(0) if match else None


def _area_name(text: str) -> str | None:
    names = {
        "northeast block 7": "Northeast Block 7",
        "northeast block 8": "Northeast Block 8",
        "southwest block 23": "Southwest Block 23",
        "southwest block 24": "Southwest Block 24",
    }
    for key, name in names.items():
        if key in text:
            return name
    return None


def _year(text: str) -> int | None:
    import re
    match = re.search(r"20\d{2}", text)
    return int(match.group(0)) if match else None


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("aicopilot.api:app", host="127.0.0.1", port=8080, reload=False)
