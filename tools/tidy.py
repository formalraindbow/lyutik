#!/usr/bin/env python3
"""Уборка памяти после сна: что в ней разъехалось, пока он жил.

    python3 tools/tidy.py lyutik

Ничего не правит — только показывает: дубли заметок, ссылки в никуда, даты из будущего, цитаты без следа в журналах,
разросшееся состояние. Правит он сам: это его память. Цикл зовёт это при пробуждении по своему плану и по инструменту tidy.
"""
import collections, datetime, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent" / "scripts"))

STATE_LIMIT = 4500


def norm_name(s):
    return re.sub(r"[\s_]+", " ", s.strip().lower().replace("ё", "е"))

def report(home, now=None):
    now = now or datetime.datetime.now()
    rows = []
    d = home / "memory" / "entities"
    files = sorted(d.glob("*.md")) if d.exists() else []
    texts = {f: f.read_text(encoding="utf-8", errors="replace") for f in files}
    core = {p: p.read_text(encoding="utf-8", errors="replace") for p in (home / "memory" / "state.md", home / "GOALS.md", home / "PERSONA.md") if p.exists()}

    groups = collections.defaultdict(list)             # дубли: одно имя в разном написании
    for f in files:
        groups[norm_name(f.stem)].append(f)
    for v in groups.values():
        if len(v) > 1:
            stubs = [f for f in v if len(texts[f]) < 400 and "[[" in texts[f]]
            if len(stubs) < len(v) - 1:
                rows.append("дубли: " + ", ".join(f.name for f in v) + " — свести в один, второй оставить указателем")

    names = set(groups)                                 # ссылки в никуда
    broken = collections.Counter()
    for text in list(texts.values()) + list(core.values()):
        for link in re.findall(r"\[\[([^\]|#]+)", text):
            if norm_name(link) not in names:
                broken[link.strip()] += 1
    if broken:
        rows.append("ссылки в никуда: " + ", ".join(f"[[{k}]]" + (f" ×{v}" if v > 1 else "") for k, v in broken.most_common(10))
                    + " — завести заметку или поправить имя")

    future = collections.Counter()                     # даты из будущего
    today = now.date()
    for f, text in list(texts.items()) + list(core.items()):
        for m in set(re.findall(r"\b20\d\d-\d\d-\d\d\b", text)):
            try:
                if datetime.date.fromisoformat(m) > today:
                    future[(Path(f).name, m)] += 1
            except ValueError:
                pass
    if future:
        rows.append("даты из будущего (сегодня " + f"{today:%Y-%m-%d}" + "): " + ", ".join(f"{m} в {name}" for (name, m), _ in future.most_common(8)))

    try:                                                # цитаты без следа в журналах
        from _truth import check, ledger
        book = ledger()
        missing = []
        for f, text in texts.items():
            _, miss = check(text, book)
            missing += [(f.name, m) for m in miss]
        if missing:
            rows.append(f"цитат без следа в журналах: {len(missing)} — " +
                        "; ".join(f"«{m[:50]}» ({name})" for name, m in missing[:6]) +
                        (" …" if len(missing) > 6 else "") + " — это по памяти, не дословно")
    except Exception as e:
        rows.append(f"сверка цитат не удалась: {type(e).__name__}")

    state = core.get(home / "memory" / "state.md", "")
    if len(state) > STATE_LIMIT:
        rows.append(f"state.md — {len(state)} знаков: сжать, оставить настроение, нити и уроки")
    goals = core.get(home / "GOALS.md", "")
    if len(goals) > 6000:
        rows.append(f"GOALS.md — {len(goals)} знаков, пунктов DONE: {goals.count('DONE')} — убрать отработанное за давностью")
    return rows


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "lyutik"
    rows = report(ROOT / "agent" / name)
    print("\n".join("- " + r for r in rows) if rows else "память чистая")


if __name__ == "__main__":
    main()
