"""Тонкая обёртка над Claude API: исследование с веб-поиском и структурированный вывод."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_PAUSE_RESTARTS = 5


class RefusalError(RuntimeError):
    pass


@dataclass
class ResearchResult:
    text: str
    urls: set[str] = field(default_factory=set)  # все URL, которые реально встретились в поиске


class LLM:
    def __init__(self, model: str | None = None):
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("CONTENT_AGENT_MODEL", DEFAULT_MODEL)
        # Серверный fallback при отказе классификатора безопасности. Отключить: CONTENT_AGENT_FALLBACKS=0
        self.fallbacks = os.environ.get("CONTENT_AGENT_FALLBACKS", "1") != "0"

    def _extra(self) -> dict:
        if not self.fallbacks:
            return {}
        return {
            "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
            "extra_body": {"fallbacks": "default"},
        }

    @staticmethod
    def _check(message) -> None:
        if message.stop_reason == "refusal":
            raise RefusalError(f"Модель отказалась выполнить запрос: {message.stop_details}")

    def research(self, system: str, prompt: str, *, max_searches: int = 15,
                 effort: str = "high") -> ResearchResult:
        """Агентный проход с веб-поиском и загрузкой страниц. Возвращает конспект и найденные URL."""
        tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_searches},
        ]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        urls: set[str] = set()
        texts: list[str] = []
        for _ in range(MAX_PAUSE_RESTARTS + 1):
            with self.client.messages.stream(
                model=self.model,
                max_tokens=64000,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": effort},
                **self._extra(),
            ) as stream:
                message = stream.get_final_message()
            self._check(message)
            for block in message.content:
                if block.type == "text":
                    texts.append(block.text)
                    for c in getattr(block, "citations", None) or []:
                        if getattr(c, "url", None):
                            urls.add(c.url)
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                    urls.update(r.url for r in block.content if getattr(r, "url", None))
                elif block.type == "web_fetch_tool_result":
                    url = getattr(block.content, "url", None)
                    if url:
                        urls.add(url)
            if message.stop_reason != "pause_turn":
                break
            # Сервер приостановил длинный ход — продолжаем с того же места
            messages = messages + [{"role": "assistant", "content": message.content}]
        return ResearchResult(text="\n".join(texts), urls=urls)

    def parse(self, system: str, prompt: str, schema: Type[T], *, effort: str = "medium") -> T:
        """Один вызов со структурированным выводом в pydantic-схему."""
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": effort},
            **self._extra(),
        )
        self._check(response)
        if response.parsed_output is None:
            raise RuntimeError(f"Не удалось разобрать ответ модели (stop_reason={response.stop_reason})")
        return response.parsed_output
