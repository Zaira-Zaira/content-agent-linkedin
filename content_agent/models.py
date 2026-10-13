"""Схемы данных конвейера. Используются и для structured outputs, и для хранения."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Segment = Literal["sell_side", "buy_side", "ecosystem"]
Role = Literal["client", "partner", "network"]


# --- Этап 1. Разведчик ---------------------------------------------------------

class Scores(BaseModel):
    fit: int = Field(description="Попадание в цель, 0-5")
    fresh: int = Field(description="Свежий повод за 30-60 дней, 0-5")
    unique: int = Field(description="Делают не так, как все, 0-5")
    reader_value: int = Field(description="Польза для читателя, 0-5")
    reaction: int = Field(description="Вероятность реакции героя, 0-5")
    topic_link: int = Field(description="Связь с темами автора, 0-5")

    @property
    def total(self) -> int:
        return sum(max(0, min(5, v)) for v in self.model_dump().values())


class Candidate(BaseModel):
    hero: str = Field(description="Имя человека или название компании")
    company: str = Field(description="Компания (для человека) или та же компания")
    handle: str = Field(description="@аккаунт или URL профиля LinkedIn для отметки; пусто, если не найден")
    segment: Segment
    role: Role
    occasion: str = Field(description="Свежий повод одной фразой")
    occasion_date: str = Field(description="Дата повода YYYY-MM-DD, если известна")
    source_urls: List[str]
    why: str = Field(description="Почему это сильный герой для поста")
    scores: Scores


class ScoutResult(BaseModel):
    candidates: List[Candidate]


# --- Этап 2. Карточка героя ----------------------------------------------------

class Fact(BaseModel):
    fact: str
    number: str = Field(description="Ключевая цифра факта, если есть, иначе пусто")
    date: str
    source_url: str


class Quote(BaseModel):
    text: str = Field(description="Дословно, до 15 слов")
    source_url: str


class ResearchCard(BaseModel):
    """Часть карточки, которую заполняет исследователь."""
    hero: str
    handle: str
    segment: Segment
    role: Role
    one_liner: str = Field(description="Кто это и что делает, одной фразой")
    facts: List[Fact]
    quotes: List[Quote]
    private_info_used: bool = Field(description="Использованы ли заметки с приватного созвона")


class Analysis(BaseModel):
    """Часть карточки, которую заполняет аналитик."""
    different_from_market: str
    risky_decision: str
    lens: str
    insight: str
    lesson_for_reader: str
    our_angle: str
    passes_uniqueness: bool
    uniqueness_reasoning: str
    missing_data: List[str]


class HeroCard(BaseModel):
    """Полная карточка героя — формат из инструкции (этап 2)."""
    hero: str
    handle: str
    segment: Segment
    role: Role
    one_liner: str
    facts: List[Fact]
    quotes: List[Quote]
    different_from_market: str = ""
    risky_decision: str = ""
    lens: str = ""
    insight: str = ""
    lesson_for_reader: str = ""
    our_angle: str = ""
    private_info_used: bool = False
    hero_consent: bool = False  # согласие героя на использование приватного разговора


# --- Этап 3. Автор -------------------------------------------------------------

class SelfCheck(BaseModel):
    banned_words: int
    length_a: int
    length_b: int
    unique_insight: bool
    all_facts_sourced: bool


class Draft(BaseModel):
    status: Literal["OK", "NEED_MORE_DATA"]
    missing_data: List[str]
    variant_a: str
    variant_b: str
    hook_type_a: str
    hook_type_b: str
    facts_used: List[str] = Field(description="source_url использованных фактов")
    target_segment: Segment
    expected_action: str
    first_comment: str = Field(description="Первый комментарий: ссылки на источники")
    self_check: SelfCheck


# --- Этап 4. Редактор ----------------------------------------------------------

class SemanticVerdict(BaseModel):
    facts_grounded: bool
    hook_standalone: bool
    unique_insight: bool
    lesson_and_question: bool
    no_irony_at_hero: bool
    confidentiality: bool
    no_hidden_sale: bool
    voice: bool
    fixes: List[str] = Field(description="Конкретные правки, если что-то не прошло")


class EditorReview(BaseModel):
    variant_a: SemanticVerdict
    variant_b: SemanticVerdict


# --- Обучение -----------------------------------------------------------------

class BestExample(BaseModel):
    post_text: str
    why_it_worked: str


class Learning(BaseModel):
    best_examples: List[BestExample]
    do_more: List[str]
    do_less: List[str]
    scout_adjustments: List[str]
    summary: str


class Metrics(BaseModel):
    inbound_messages: int = 0      # главная метрика
    hero_reacted: bool = False
    target_comments: int = 0       # комментарии от ЛПР, основателей, партнёров
    comments_total: int = 0
    saves: int = 0
    reposts: int = 0
    impressions: int = 0
    new_target_followers: int = 0
    notes: Optional[str] = None
