"""Пять агентов конвейера + агент обучения. Каждый — функция: вход → выход из таблицы инструкции."""
from __future__ import annotations

import json

from . import storage
from .checks import check_card, check_facts_used, check_post
from .llm import LLM, ResearchResult
from .models import (Analysis, Candidate, Draft, EditorReview, HeroCard, Learning, ResearchCard,
                     ScoutResult)


def _bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items)


def _segments_text(profile: dict) -> str:
    return "\n".join(
        f"- {key} ({s['label']}): {s['who']}. Им важно увидеть: {s['wants']}"
        for key, s in profile["segments"].items()
    )


def _dump(model) -> str:
    return json.dumps(model if isinstance(model, dict) else model.model_dump(), ensure_ascii=False, indent=2)


# --- 1. Разведчик ---------------------------------------------------------------

def segment_need(profile: dict) -> str:
    counts = storage.segment_counts_this_month()
    total = sum(counts.values()) or 1
    parts = []
    for key, s in profile["segments"].items():
        gap = s["share"] - counts.get(key, 0) / total if sum(counts.values()) else s["share"]
        parts.append(f"{key}: целевая доля {int(s['share'] * 100)}%, в этом месяце уже {counts.get(key, 0)} "
                     f"постов{' — нужно больше' if gap > 0.05 else ''}")
    return "; ".join(parts)


def scout(llm: LLM, profile: dict, n: int = 8, focus: str = "") -> list[Candidate]:
    rules = profile["rules"]
    recent = storage.recent_heroes(rules["hero_cooldown_days"])
    stop = profile.get("stoplist", {})
    system = storage.prompt("scout").format(
        segments=_segments_text(profile),
        topics=_bullets(profile["topics"]),
        sources=_bullets(profile["sources"]),
        stoplist=_bullets(stop.get("companies", []) + stop.get("topics", [])) or "—",
        recent_heroes=", ".join(recent) or "—",
        fresh_days=rules["fresh_news_days"],
        n=n,
        segment_need=segment_need(profile),
    )
    task = f"Найди {n} кандидатов в герои постов." + (f" Фокус: {focus}" if focus else "")
    adjustments = storage.scout_adjustments()
    if adjustments:
        task += f"\n\nВыводы из метрик прошлых постов для отбора:\n{adjustments}"
    found = llm.research(system, task, max_searches=20)
    result = llm.parse(
        system,
        "Оформи найденных кандидатов в структуру. Используй только URL из конспекта.\n\n"
        f"КОНСПЕКТ ПОИСКА:\n{found.text}\n\nURL из поиска:\n" + "\n".join(sorted(found.urls)),
        ScoutResult,
    )
    stop_companies = {c.lower() for c in stop.get("companies", [])}
    recent_l = {h.lower() for h in recent}
    out = [
        c for c in result.candidates
        if c.scores.total >= rules["min_score"]
        and c.hero.lower() not in recent_l
        and c.company.lower() not in stop_companies
        and c.hero.lower() not in stop_companies
    ]
    return sorted(out, key=lambda c: c.scores.total, reverse=True)


# --- 2. Исследователь -----------------------------------------------------------

def research(llm: LLM, candidate: dict, extra_focus: list[str] | None = None) -> tuple[ResearchCard, ResearchResult]:
    notes = storage.call_notes(candidate["hero"])
    system = storage.prompt("researcher")
    task = (
        f"Герой: {candidate['hero']} ({candidate.get('company', '')})\n"
        f"Сегмент: {candidate['segment']}, роль: {candidate['role']}\n"
        f"Повод: {candidate.get('occasion', '')}\n"
        f"Стартовые источники:\n{_bullets(candidate.get('source_urls', []))}\n"
    )
    if extra_focus:
        task += "\nПрошлого исследования не хватило. Найди особенно:\n" + _bullets(extra_focus)
    if notes:
        task += f"\n\nЗАМЕТКИ С НАШЕГО СОЗВОНА (приватно, source_url = private:call_notes):\n{notes}"
    found = llm.research(system, task, max_searches=25)
    card = llm.parse(
        system,
        "Заполни карточку героя строго по конспекту. Только факты со ссылкой из конспекта. "
        f"handle — как в кандидате, если не нашёл точнее: {candidate.get('handle', '')}\n\n"
        f"КАНДИДАТ:\n{_dump(candidate)}\n\nКОНСПЕКТ:\n{found.text}\n\nURL из поиска:\n"
        + "\n".join(sorted(found.urls)),
        ResearchCard,
    )
    return card, found


# --- 3. Аналитик ----------------------------------------------------------------

def analyze(llm: LLM, profile: dict, card: ResearchCard) -> Analysis:
    system = storage.prompt("analyst").format(niche_lenses=_bullets(profile["niche_lenses"]))
    return llm.parse(system, f"КАРТОЧКА ГЕРОЯ:\n{_dump(card)}", Analysis, effort="high")


def merge_card(card: ResearchCard, analysis: Analysis) -> HeroCard:
    data = card.model_dump()
    data.update({k: getattr(analysis, k) for k in
                 ("different_from_market", "risky_decision", "lens", "insight", "lesson_for_reader", "our_angle")})
    return HeroCard(**data)


# --- 4. Автор -------------------------------------------------------------------

def author_system(profile: dict) -> str:
    system = storage.prompt("author").format(
        name=profile["author"]["name"],
        keywords=", ".join(profile["keywords"]),
        hook_examples=_bullets(profile["hook_examples"]),
    )
    learned = storage.learned_block()
    if learned:
        system += "\n\nВЫВОДЫ ИЗ МЕТРИК ПРОШЛЫХ ПОСТОВ (образцы и приёмы)\n" + learned
    return system


def goal_block(goal: str) -> str:
    if not goal:
        return ""
    return ("\n\nCAMPAIGN GOAL (the business task this post serves; written in Russian): " + goal +
            "\nThe post must make the people this goal needs notice the author and see her as the person to talk to, "
            "without selling. NEVER mention, describe or hint at the specific asset, client or deal behind this goal.")


def write(llm: LLM, profile: dict, card: HeroCard, feedback: str = "", goal: str = "") -> Draft:
    task = f"КАРТОЧКА ГЕРОЯ:\n{_dump(card)}{goal_block(goal)}"
    if feedback:
        task += f"\n\nРЕДАКТОР ВЕРНУЛ ЧЕРНОВИК. Исправь:\n{feedback}"
    return llm.parse(author_system(profile), task, Draft, effort="high")


# --- 5. Редактор ----------------------------------------------------------------

SEMANTIC_KEYS = ("facts_grounded", "hook_standalone", "unique_insight", "lesson_and_question",
                 "no_irony_at_hero", "confidentiality", "no_hidden_sale", "voice")


def edit(llm: LLM, profile: dict, card: HeroCard, draft: Draft) -> dict:
    """Возвращает отчёт: формальные + смысловые проверки по каждому варианту и общий вердикт."""
    c = card.model_dump()
    sources = check_facts_used(draft.facts_used, c)
    formal = {
        v: check_post(getattr(draft, f"variant_{v}"), hero=card.hero, handle=card.handle,
                      keywords=profile["keywords"]).as_dict()
        for v in ("a", "b")
    }
    semantic: EditorReview = llm.parse(
        storage.prompt("editor"),
        f"КАРТОЧКА ГЕРОЯ:\n{_dump(c)}\n\nВАРИАНТ A:\n{draft.variant_a}\n\nВАРИАНТ B:\n{draft.variant_b}\n\n"
        f"ФОРМАЛЬНЫЕ ПРОВЕРКИ:\n{_dump(formal)}",
        EditorReview,
        effort="high",
    )
    report = {"sources": sources.as_dict(), "variants": {}}
    for v in ("a", "b"):
        sem = getattr(semantic, f"variant_{v}")
        failed = [k for k in SEMANTIC_KEYS if not getattr(sem, k)]
        report["variants"][v] = {
            "formal": formal[v],
            "semantic_failed": failed,
            "fixes": sem.fixes,
            "passed": formal[v]["ok"] and not failed and sources.ok,
        }
    report["passed"] = any(x["passed"] for x in report["variants"].values())
    return report


def feedback_from(report: dict) -> str:
    lines = [f"Источники: {e}" for e in report["sources"]["errors"]]
    for v, r in report["variants"].items():
        for e in r["formal"]["errors"]:
            lines.append(f"Вариант {v.upper()}: {e}")
        for k in r["semantic_failed"]:
            lines.append(f"Вариант {v.upper()}: не пройдено «{k}»")
        for fx in r["fixes"]:
            lines.append(f"Вариант {v.upper()}: {fx}")
    return "\n".join(lines)


# --- Обучение -------------------------------------------------------------------

def learn(llm: LLM, posts: list[dict]) -> Learning:
    rows = [{
        "hero": p["hero"], "segment": p["segment"], "lens": p["card"].get("lens"),
        "hook_type": p.get("final", {}).get("hook_type"), "text": p.get("final", {}).get("text"),
        "metrics": p.get("metrics"),
    } for p in posts]
    return llm.parse(storage.prompt("learner"), f"ПОСТЫ И МЕТРИКИ:\n{_dump({'posts': rows})}", Learning,
                     effort="high")


def learned_markdown(l: Learning) -> str:
    parts = ["## Образцы, которые сработали"]
    for ex in l.best_examples:
        parts.append(f"Почему сработал: {ex.why_it_worked}\n---\n{ex.post_text}\n---")
    parts += ["## Усиливать", _bullets(l.do_more), "## Убрать", _bullets(l.do_less)]
    return "\n\n".join(parts) + "\n"
