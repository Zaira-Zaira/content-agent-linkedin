"""Оркестрация конвейера с двумя возвратами:
банальный инсайт → обратно на исследование; ошибка редактора → обратно автору."""
from __future__ import annotations

from datetime import date

from . import agents, storage
from .checks import check_card
from .llm import LLM


def log(msg: str) -> None:
    print(f"  · {msg}", flush=True)


def process_candidate(llm: LLM, profile: dict, candidate: dict, goal: str = "") -> dict:
    """Кандидат → проверенные черновики в очереди на решение человека."""
    rules = profile["rules"]
    post = {
        "id": storage.new_post_id(candidate["hero"]),
        "created": f"{date.today():%Y-%m-%d}",
        "hero": candidate["hero"],
        "segment": candidate["segment"],
        "candidate": candidate,
        "status": "in_progress",
        "history": [],
        "goal": goal or None,
    }

    # Этап 2: исследование + анализ, с возвратом при банальном инсайте
    focus: list[str] | None = None
    card = None
    for attempt in range(rules["max_research_loops"] + 1):
        log(f"Исследователь: {candidate['hero']} (попытка {attempt + 1})")
        research_card, found = agents.research(llm, candidate, focus)
        card_check = check_card(research_card.model_dump(), found.urls)
        post["history"].append({"step": "research", "card_check": card_check.as_dict()})
        if card_check.errors:
            # убираем факты/цитаты без ссылок, остальные ошибки увидит человек
            research_card.facts = [f for f in research_card.facts if f.source_url.strip()]
            research_card.quotes = [q for q in research_card.quotes
                                    if q.source_url.strip() and len(q.text.split()) <= 15]
        log("Аналитик: ищу линзу и инсайт")
        analysis = agents.analyze(llm, profile, research_card)
        post["history"].append({"step": "analysis", "analysis": analysis.model_dump()})
        if analysis.passes_uniqueness:
            card = agents.merge_card(research_card, analysis)
            break
        log(f"Инсайт банальный: {analysis.uniqueness_reasoning}")
        focus = analysis.missing_data
    if card is None:
        post["status"] = "rejected_banal"
        storage.save_post(post)
        log("Герой отклонён: не нашли уникального инсайта. Возьмите следующего кандидата.")
        return post
    post["card"] = card.model_dump()

    # Этап 3–4: автор ↔ редактор
    feedback = ""
    for attempt in range(rules["max_rewrite_loops"] + 1):
        log(f"Автор: 2 варианта поста (попытка {attempt + 1})")
        draft = agents.write(llm, profile, card, feedback, goal)
        if draft.status == "NEED_MORE_DATA":
            post["status"] = "need_more_data"
            post["missing_data"] = draft.missing_data
            storage.save_post(post)
            log("Автору не хватает фактов: " + "; ".join(draft.missing_data))
            return post
        log("Редактор: автопроверки")
        report = agents.edit(llm, profile, card, draft)
        post["draft"] = draft.model_dump()
        post["editor"] = report
        post["history"].append({"step": "edit", "passed": report["passed"]})
        if report["passed"]:
            break
        feedback = agents.feedback_from(report)
        log("Возврат автору:\n    " + feedback.replace("\n", "\n    "))

    post["status"] = "ready_for_review" if post["editor"]["passed"] else "needs_human_fix"
    storage.save_post(post)
    log(f"Готово: {post['id']} → {post['status']}")
    return post


def run(llm: LLM, profile: dict, n_posts: int, n_candidates: int = 8, focus: str = "", goal: str = "") -> list[dict]:
    log("Разведчик: ищу героев" + (f" под цель «{goal}»" if goal else ""))
    if goal:
        focus = (f"ЦЕЛЬ: {goal}. Ищи людей и компании, которые сами относятся к нужной для этой цели аудитории "
                 f"или которых эта аудитория уважает, с публичным свежим поводом. " + focus).strip()
    candidates = agents.scout(llm, profile, n_candidates, focus)
    data = [c.model_dump() | {"total": c.scores.total} for c in candidates]
    path = storage.save_candidates(data)
    log(f"Кандидатов от {profile['rules']['min_score']}/30: {len(data)} (сохранено в {path})")
    results = []
    for c in data:
        if sum(p["status"] in {"ready_for_review", "needs_human_fix"} for p in results) >= n_posts:
            break
        results.append(process_candidate(llm, profile, c, goal))
    return results
