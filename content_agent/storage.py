"""Хранение состояния конвейера в JSON-файлах (data/)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROMPTS = Path(__file__).resolve().parent / "prompts"


def load_profile(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT / "config" / "profile.yaml").read_text(encoding="utf-8"))


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return s[:60] or "hero"


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --- Кандидаты ---------------------------------------------------------------

def save_candidates(items: list[dict]) -> Path:
    path = DATA / "candidates" / f"{datetime.now():%Y-%m-%d_%H%M}.json"
    _write(path, items)
    return path


def latest_candidates() -> list[dict]:
    files = sorted((DATA / "candidates").glob("*.json"))
    return _read(files[-1], []) if files else []


# --- Посты ------------------------------------------------------------------
# Один пост = одна папка data/posts/<id>/ с post.json (карточка, черновики, отчёты, статус, метрики)

def post_dir(post_id: str) -> Path:
    return DATA / "posts" / post_id


def new_post_id(hero: str) -> str:
    return f"{date.today():%Y-%m-%d}_{slug(hero)}"


def save_post(post: dict) -> Path:
    path = post_dir(post["id"]) / "post.json"
    _write(path, post)
    return path


def load_post(post_id: str) -> dict:
    path = post_dir(post_id) / "post.json"
    if not path.exists():
        raise FileNotFoundError(f"Пост {post_id} не найден")
    return _read(path, {})


def all_posts() -> list[dict]:
    return [_read(p, {}) for p in sorted((DATA / "posts").glob("*/post.json"))]


# --- История героев (одного героя не чаще раза в N дней) ----------------------

def recent_heroes(days: int) -> list[str]:
    cutoff = date.today() - timedelta(days=days)
    out = []
    for p in all_posts():
        if p.get("status") in {"approved", "published"} and date.fromisoformat(p["created"]) >= cutoff:
            out.append(p["hero"])
    return out


def segment_counts_this_month() -> dict[str, int]:
    month = f"{date.today():%Y-%m}"
    counts = {"sell_side": 0, "buy_side": 0, "ecosystem": 0}
    for p in all_posts():
        if p.get("status") in {"approved", "published"} and p["created"].startswith(month):
            counts[p["segment"]] = counts.get(p["segment"], 0) + 1
    return counts


# --- Заметки созвонов и выученные приёмы --------------------------------------

def call_notes(hero: str) -> str:
    """Заметки с созвона кладутся вручную в data/call_notes/<slug героя>.md"""
    path = DATA / "call_notes" / f"{slug(hero)}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def learned_block() -> str:
    path = DATA / "learned.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def scout_adjustments() -> str:
    path = DATA / "scout_adjustments.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def save_scout_adjustments(text: str) -> None:
    (DATA / "scout_adjustments.md").write_text(text, encoding="utf-8")


def save_learned(text: str) -> Path:
    path = DATA / "learned.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    hist = DATA / "learning_history" / f"{date.today():%Y-%m-%d}.md"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(text, encoding="utf-8")
    return path
