"""Автоматические формальные проверки агента-редактора (этап 4). Без LLM — детерминированно."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BANNED_PHRASES = [
    # English
    "in today's world", "it's no secret", "plays a key role", "unique opportunity", "worth noting",
    "it's important to understand", "let's dive", "deep dive", "dive into", "in conclusion", "innovative",
    "game-changer", "game changer", "synergy", "next level", "inspiring", "true professionals", "dream team",
    "hi everyone", "today i want to share", "genius", "incredible", "the best",
    # Русские
    "в современном мире", "не секрет", "играет ключевую роль", "уникальная возможность",
    "стоит отметить", "важно понимать", "в заключение", "давайте разберёмся", "давайте разберемся",
    "погрузимся", "инновационн", "эффективные решения", "на новый уровень", "новый уровень",
    "синерги", "вдохновляет", "настоящие профессионалы", "команда мечты",
    "друзья, всем привет", "сегодня хочу рассказать",
    # оценки без доказательств
    "гениальн", "невероятн", "лучший", "лучшая", "лучшее",
]

FORBIDDEN_STARTS = [
    "today i want to share", "hi everyone", "hello everyone", "in today's world", "it's no secret",
    "сегодня хочу рассказать", "друзья, всем привет", "друзья", "в современном мире", "не секрет",
]

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]"
)
MENTION_RE = re.compile(r"(?<![\w@])@[\w.\-]+", re.UNICODE)
HASHTAG_RE = re.compile(r"(?<!\w)#[\w\-]+", re.UNICODE)
URL_RE = re.compile(r"https?://\S+|www\.\S+")

MIN_LEN, OPT_MAX, MAX_LEN = 600, 1300, 1800
MAX_PARA_LINES = 3
MAX_PARA_CHARS = 320  # ≈ 3 строки в ленте на телефоне
MAX_QUOTE_WORDS = 15


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


def check_post(text: str, *, hero: str, handle: str, keywords: list[str]) -> CheckResult:
    r = CheckResult()
    body = text.strip()
    low = body.lower()

    # Длина
    n = len(body)
    if n < MIN_LEN or n > MAX_LEN:
        r.errors.append(f"Длина {n} знаков, нужно {MIN_LEN}–{MAX_LEN}")
    elif n > OPT_MAX:
        r.warnings.append(f"Длина {n} знаков — выше оптимума {OPT_MAX}")

    # Абзацы
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    for i, p in enumerate(paragraphs, 1):
        if HASHTAG_RE.fullmatch(p.strip().split()[0]) and all(HASHTAG_RE.fullmatch(w) for w in p.split()):
            continue  # блок хэштегов
        lines = [l for l in p.splitlines() if l.strip()]
        if len(lines) > MAX_PARA_LINES or len(p) > MAX_PARA_CHARS:
            r.errors.append(f"Абзац {i} слишком длинный ({len(lines)} строк, {len(p)} знаков)")

    # Стоп-лист
    found = [w for w in BANNED_PHRASES if w in low]
    if found:
        r.errors.append("Слова из стоп-листа: " + ", ".join(found))

    # Хук
    first_line = body.splitlines()[0].strip().lower() if body else ""
    if any(first_line.startswith(s) for s in FORBIDDEN_STARTS):
        r.errors.append("Хук начинается с запрещённой фразы")
    head = "\n".join(body.splitlines()[:2]).lower()
    if not re.search(r"\d", head):
        r.errors.append("В первых двух строках нет цифры")
    if not any(k.lower() in head for k in keywords):
        r.warnings.append("В первых двух строках нет ключевого слова из списка")

    # Пунктуация и эмодзи
    if body.count("!") > 1:
        r.errors.append(f"Восклицательных знаков: {body.count('!')} (максимум 1)")
    emojis = EMOJI_RE.findall(body)
    if len(emojis) > 3:
        r.errors.append(f"Эмодзи: {len(emojis)} (максимум 3)")

    # Отметки
    mentions = MENTION_RE.findall(body)
    if len(mentions) > 3:
        r.errors.append(f"Отметок: {len(mentions)} (максимум 3)")
    h = handle.strip()
    if h.startswith("@"):
        if h.lower() not in low:
            r.errors.append(f"Герой не отмечен ({h})")
    elif hero.split()[0].lower() not in low:
        r.errors.append(f"Герой не упомянут ({hero})")
    elif not mentions:
        r.warnings.append("Нет @-отметки: отметьте героя вручную при публикации")

    # Хэштеги
    tags = HASHTAG_RE.findall(body)
    if not 3 <= len(tags) <= 5:
        r.errors.append(f"Хэштегов: {len(tags)} (нужно 3–5 в конце)")

    # Ссылки — в первый комментарий
    if URL_RE.search(body):
        r.errors.append("Ссылка в тексте поста — перенесите в первый комментарий")

    # Финальный вопрос / приглашение
    tail = "\n".join(l for l in body.splitlines() if l.strip() and not l.strip().startswith("#"))[-300:]
    invite = re.search(r"пишите|в личк|\b(dm|message me|drop me|reach out|let's talk)\b", tail.lower())
    if "?" not in tail and not invite:
        r.warnings.append("Нет финального вопроса или приглашения в диалог")

    return r


def check_card(card: dict, research_urls: set[str] | None = None) -> CheckResult:
    """Каждый факт со ссылкой; цитаты до 15 слов; ссылки реально встретились в поиске."""
    r = CheckResult()
    for f in card.get("facts", []):
        url = f.get("source_url", "").strip()
        if not url:
            r.errors.append(f"Факт без ссылки: {f.get('fact', '')[:80]}")
        elif research_urls is not None and not url.startswith("private:") and url not in research_urls:
            r.warnings.append(f"Ссылка не встречалась в результатах поиска: {url}")
    for q in card.get("quotes", []):
        if not q.get("source_url"):
            r.errors.append(f"Цитата без ссылки: {q.get('text', '')[:80]}")
        if len(q.get("text", "").split()) > MAX_QUOTE_WORDS:
            r.errors.append(f"Цитата длиннее {MAX_QUOTE_WORDS} слов: {q.get('text', '')[:80]}")
    if card.get("private_info_used") and not card.get("hero_consent"):
        r.errors.append("Использованы данные из личного разговора без согласия героя")
    return r


def check_facts_used(facts_used: list[str], card: dict) -> CheckResult:
    r = CheckResult()
    known = {f["source_url"] for f in card.get("facts", [])} | {q["source_url"] for q in card.get("quotes", [])}
    for url in facts_used:
        if url not in known:
            r.errors.append(f"Источник не из карточки: {url}")
    if any(u.startswith("private:") for u in facts_used) and not card.get("hero_consent"):
        r.errors.append("Пост опирается на приватные данные без согласия героя")
    return r
