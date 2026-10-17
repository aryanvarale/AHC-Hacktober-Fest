"""Runtime settings. MODE selects the adapter set; only lite is implemented."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv() -> None:
    path = ROOT / "backend" / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

DATA_DIR = Path(os.environ.get("AICOPILOT_DATA", ROOT / "test-data" / "dataset"))
DB_PATH = Path(os.environ.get("AICOPILOT_DB", ROOT / "backend" / "data" / "field.duckdb"))
TODAY = date.fromisoformat(os.environ.get("AICOPILOT_TODAY", "2027-01-15"))


@dataclass(frozen=True)
class Settings:
    mode: str = os.environ.get("AICOPILOT_MODE", "lite")
    data_dir: Path = DATA_DIR
    db_path: Path = DB_PATH
    today: date = TODAY
    model: str = os.environ.get("AICOPILOT_MODEL", "qwen3.6-27b")
    base_url: str = os.environ.get("AICOPILOT_BASE_URL", "")
    router_model: str = os.environ.get("AICOPILOT_ROUTER_MODEL", "gemma-4-12b")
    router_base_url: str = os.environ.get("AICOPILOT_ROUTER_BASE_URL", "")
    api_key: str = os.environ.get("AICOPILOT_API_KEY", "local")


def get_settings() -> Settings:
    return Settings()
