#!/usr/bin/env python3
"""Дописать датированную строку в заметку о человеке, месте, вещи или событии.

    remember.py <имя> "<что запомнить>"

Заметка ищется по имени без учёта регистра, пробелов и подчёркиваний: «Эсси Давен» и «эсси_давен» — одна
заметка. Нет такой — создаётся под именем как есть. Дата ставится сама, повторять её в тексте не нужно.
"""
import re, sys
from _common import HOME, now
from _truth import check, mark


def norm(s):
    return re.sub(r"[\s_]+", " ", s.strip().lower())

def main():
    if len(sys.argv) < 3 or not sys.argv[2].strip():
        print('не записано: использование remember.py <имя> "<что запомнить>"')
        sys.exit(1)
    name = sys.argv[1].strip()
    if not re.fullmatch(r"[\w\- .]{1,60}", name):
        print("не записано: имя — буквы, цифры, пробел, дефис, точка, до 60 знаков")
        sys.exit(1)
    d = HOME / "memory" / "entities"
    d.mkdir(parents=True, exist_ok=True)
    same = [f for f in d.glob("*.md") if norm(f.stem) == norm(name)]
    path = max(same, key=lambda f: f.stat().st_size) if same else d / f"{name}.md"  # из дублей — полная, не заглушка
    today, text = f"{now():%Y-%m-%d}", sys.argv[2].strip()
    if text.startswith(today):  # сегодняшнюю дату ставлю сам
        text = text[len(today):].lstrip(" ,.:·—–-")
    found, missing = check(text)                       # книга учёта: цитаты сверяются с журналами
    text = mark(text, missing)
    line = f"- {today} — {text}"
    ledger_note = ""
    if found or missing:
        ledger_note = f"\nцитаты: дословно {len(found)}, без следа в журнале {len(missing)}"
        if missing:
            ledger_note += " — помечены «(по памяти)»: " + "; ".join(f"«{m[:60]}»" for m in missing[:4])
    if not path.exists():
        path.write_text(f"# {name}\nс: {now():%Y-%m-%d}\n\n## События\n{line}\n\n## Связи\n", encoding="utf-8")
        print(f"новая заметка {path.relative_to(HOME)}: {line}{ledger_note}")
        return
    body = path.read_text(encoding="utf-8")
    if "## События" in body:
        head, tail = body.split("## События", 1)
        rest = tail.split("\n## ", 1)
        body = head + "## События" + rest[0].rstrip("\n") + "\n" + line + "\n" + ("\n## " + rest[1] if len(rest) > 1 else "")
    else:
        body = body.rstrip("\n") + "\n\n## События\n" + line + "\n"
    path.write_text(body, encoding="utf-8")
    print(f"записано в {path.relative_to(HOME)}: {line}{ledger_note}")


if __name__ == "__main__":
    main()
