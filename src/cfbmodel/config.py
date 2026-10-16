"""Paths, settings and secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(os.environ.get("CFB_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REFERENCE_DIR = DATA_DIR / "reference"
MODELS_DIR = DATA_DIR / "models"
REPORTS_DIR = ROOT / "reports"
DB_PATH = Path(os.environ.get("CFB_DB", DATA_DIR / "cfb.duckdb"))


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


@lru_cache
def sources() -> dict[str, Any]:
    return _load_yaml("sources.yaml")


@lru_cache
def settings() -> dict[str, Any]:
    return _load_yaml("settings.yaml")


@dataclass(frozen=True)
class Secrets:
    cfbd_api_key: str | None
    google_api_key: str | None
    odds_api_key: str | None


def secrets() -> Secrets:
    load_dotenv(ROOT / ".env")
    return Secrets(
        cfbd_api_key=os.environ.get("CFBD_API_KEY") or None,
        google_api_key=os.environ.get("GOOGLE_API_KEY") or None,
        odds_api_key=os.environ.get("ODDS_API_KEY") or None,
    )


def require(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value
