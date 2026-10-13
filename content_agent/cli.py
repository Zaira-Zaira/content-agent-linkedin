"""Командная строка конвейера: python -m content_agent <команда>"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

from . import agents, pipeline, storage
from .checks import check_post
from .llm import LLM
from .models import Metrics

STATUS_RU = {
    "ready_for_review": "ждёт решения человека",
    "needs_human_fix": "редактор не пропустил — нужна правка человеком",
    "need_more_data": "не хватает фактов",
    "rejected_banal": "отклонён: банальный инсайт",
    "approved": "одобрен, ждёт публикации",
    "published": "опубликован",
}


def cmd_scout(args, profile):
    cands = agents.scout(LLM(args.model), profile, args.n, args.focus or "")
    data = [c.model_dump() | {"total": c.scores.total} for c in cands]
    path = storage.save_candidates(data)
    for i, c in enumerate(data):
        print(f"[{i}] {c['total']}/30  {c['hero']} ({c['segment']}, {c['role']})\n     {c['occasion']}")
    print(f"\nСохранено: {path}\nДальше: python -m content_agent process <номер>")


def cmd_process(args, profile):
    cands = storage.latest_candidates()
    if not cands:
        sys.exit("Нет кандидатов. Сначала: python -m content_agent scout")
    for i in args.indexes:
        pipeline.process_candidate(LLM(args.model), profile, cands[i])


def cmd_run(args, profile):
    pipeline.run(LLM(args.model), profile, args.posts, args.candidates, args.focus or "", args.goal or "")
    print("\nДальше: python -m content_agent list → review <id>")


def cmd_list(args, profile):
    posts = storage.all_posts()
    if not posts:
        print("Постов пока нет.")
    for p in posts:
        print(f"{p['id']:<50} {p['segment']:<10} {STATUS_RU.get(p['status'], p['status'])}")
    counts = storage.segment_counts_this_month()
    total = sum(counts.values())
    if total:
        print("\nБаланс месяца: " + ", ".join(f"{k} {v / total:.0%}" for k, v in counts.items())
              + " (цель 35/35/30)")


def _print_variant(post, v):
    d, r = post["draft"], post["editor"]["variants"][v]
    print(f"\n{'=' * 20} ВАРИАНТ {v.upper()} · хук: {d[f'hook_type_{v}']} · "
          f"{'✓ прошёл' if r['passed'] else '✗ не прошёл'} {'=' * 20}\n")
    print(d[f"variant_{v}"])
    for e in r["formal"]["errors"]:
        print(f"  ✗ {e}")
    for w in r["formal"]["warnings"]:
        print(f"  ! {w}")
    for k in r["semantic_failed"]:
        print(f"  ✗ смысловая проверка: {k}")
    for fx in r["fixes"]:
        print(f"  → {fx}")


def _edit_in_editor(text: str) -> str:
    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    subprocess.call([editor, path])
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def cmd_review(args, profile):
    """Проверка человеком (5–10 минут): выбрать вариант, поправить, добавить свою деталь, одобрить."""
    post = storage.load_post(args.id)
    if "draft" not in post:
        sys.exit(f"У поста нет черновика (статус: {post['status']})")
    card = post["card"]
    print(f"Герой: {card['hero']} {card['handle']} · сегмент: {card['segment']} · линза: {card['lens']}")
    print(f"Инсайт: {card['insight']}")
    print(f"Ожидаемое действие читателя: {post['draft']['expected_action']}")
    for e in post["editor"]["sources"]["errors"]:
        print(f"  ✗ {e}")
    _print_variant(post, "a")
    _print_variant(post, "b")

    print("\nКлючевые факты — проверьте 1–2 по ссылкам:")
    for f in card["facts"][:6]:
        print(f"  • {f['fact']} — {f['source_url']}")

    choice = input("\nВариант (a/b, пусто — отложить): ").strip().lower()
    if choice not in {"a", "b"}:
        print("Отложено.")
        return
    text = post["draft"][f"variant_{choice}"]
    print("\nСейчас откроется редактор. Прочитайте вслух — звучит ли как вы? "
          "Добавьте одну деталь из своего опыта, которую машина знать не может.")
    input("Enter — открыть редактор…")
    text = _edit_in_editor(text)

    result = check_post(text, hero=card["hero"], handle=card["handle"], keywords=profile["keywords"])
    for e in result.errors:
        print(f"  ✗ {e}")
    for w in result.warnings:
        print(f"  ! {w}")
    checklist = [
        "Прочитали вслух, звучит как вы",
        "Проверили 1–2 ключевых факта по ссылкам",
        "Добавили деталь из своего опыта",
        "Нет NDA и непубличных деталей сделок",
    ]
    for item in checklist:
        if input(f"{item}? (y/n): ").strip().lower() != "y":
            print("Не одобрено. Черновик сохранён, вернитесь позже.")
            post["final"] = {"variant": choice, "text": text, "hook_type": post["draft"][f"hook_type_{choice}"]}
            storage.save_post(post)
            return
    post["final"] = {
        "variant": choice,
        "text": text,
        "hook_type": post["draft"][f"hook_type_{choice}"],
        "first_comment": post["draft"]["first_comment"],
        "approved_at": datetime.now().isoformat(timespec="minutes"),
    }
    post["status"] = "approved"
    storage.save_post(post)
    final_path = storage.post_dir(post["id"]) / "final.md"
    final_path.write_text(f"{text}\n\n---\nПервый комментарий:\n{post['draft']['first_comment']}\n",
                          encoding="utf-8")
    print(f"\nОдобрено. Текст: {final_path}\n"
          "Опубликуйте вручную или через разрешённый площадкой инструмент, ссылки — в первый комментарий.\n"
          "В первый час ответьте на все комментарии лично. Затем: "
          f"python -m content_agent published {post['id']} --url <ссылка>")


def cmd_published(args, profile):
    post = storage.load_post(args.id)
    post["status"] = "published"
    post["published_at"] = datetime.now().isoformat(timespec="minutes")
    post["url"] = args.url
    storage.save_post(post)
    print(f"Отмечено. Через 48 часов внесите метрики: python -m content_agent metrics {post['id']}")


def cmd_metrics(args, profile):
    post = storage.load_post(args.id)
    fields = Metrics.model_fields
    values = {}
    print("Метрики через 48 часов после публикации (Enter — 0):")
    for name, f in fields.items():
        if name == "notes":
            values[name] = input("Заметки (кто написал, кто из ЛПР комментировал): ").strip() or None
        elif f.annotation is bool:
            values[name] = input(f"{name} (y/n): ").strip().lower() == "y"
        else:
            raw = input(f"{name}: ").strip()
            values[name] = int(raw) if raw else 0
    post["metrics"] = Metrics(**values).model_dump()
    storage.save_post(post)
    print("Сохранено.")


def cmd_learn(args, profile):
    """Раз в неделю: что сработало → лучшие примеры в промпт автора, слабые приёмы убираются."""
    since = date.today() - timedelta(days=args.days)
    posts = [p for p in storage.all_posts()
             if p.get("status") == "published" and p.get("metrics")
             and date.fromisoformat(p["created"]) >= since]
    if len(posts) < 2:
        sys.exit("Нужно хотя бы 2 опубликованных поста с метриками.")
    result = agents.learn(LLM(args.model), posts)
    storage.save_learned(agents.learned_markdown(result))
    storage.save_scout_adjustments("\n".join(f"- {x}" for x in result.scout_adjustments))
    print(result.summary)
    print("\nОбновлены data/learned.md (промпт автора) и data/scout_adjustments.md (отбор героев).")
    print("Раз в месяц пересматривайте портреты аудитории, темы и стоп-лист в config/profile.yaml.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="content_agent", description="Конвейер постов-анализов")
    ap.add_argument("--model", default=None, help="ID модели (по умолчанию claude-opus-5-5)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scout", help="Этап 1: найти и оценить кандидатов")
    p.add_argument("-n", type=int, default=8)
    p.add_argument("--focus")
    p.set_defaults(fn=cmd_scout)

    p = sub.add_parser("process", help="Этапы 2–4 для кандидатов из последнего поиска")
    p.add_argument("indexes", type=int, nargs="+")
    p.set_defaults(fn=cmd_process)

    p = sub.add_parser("run", help="Весь конвейер: поиск → черновики в очередь")
    p.add_argument("--posts", type=int, default=3, help="Сколько постов подготовить")
    p.add_argument("--candidates", type=int, default=8)
    p.add_argument("--focus")
    p.add_argument("--goal", help="Бизнес-задача: «найти покупателей на трофейный отель»")
    p.set_defaults(fn=cmd_run)

    sub.add_parser("list", help="Очередь и статусы").set_defaults(fn=cmd_list)

    p = sub.add_parser("review", help="Решение человека: выбор, правка, одобрение")
    p.add_argument("id")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("published", help="Отметить публикацию")
    p.add_argument("id")
    p.add_argument("--url", required=True)
    p.set_defaults(fn=cmd_published)

    p = sub.add_parser("metrics", help="Внести метрики через 48 часов")
    p.add_argument("id")
    p.set_defaults(fn=cmd_metrics)

    p = sub.add_parser("learn", help="Еженедельное обучение на метриках")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_learn)

    args = ap.parse_args(argv)
    args.fn(args, storage.load_profile())


if __name__ == "__main__":
    main()
