#!/usr/bin/env python3
"""Перечитать личку: что человек писал и что я отвечал.

    dm.py list [номер чата]     последние сообщения в личке — по одному чату или по всем

Личка живёт в state/dialogs.jsonl (dir in/out, kind dm). Отвечать — reply.py.
"""
import sys
from _common import STATE, read_jsonl

LIMIT = 25


def is_dm(d):
    """Личные сообщения. У старых исходящих текстов (до 26.10) поля kind нет — узнаём их по чату
    без поста и действия: комментарии держат post, модерация — action."""
    if d.get("kind"):
        return d.get("kind") == "dm"
    return "chat" in d and "post" not in d and "action" not in d


def listing(chat):
    rows = [d for d in read_jsonl(STATE / "dialogs.jsonl") if is_dm(d) and not d.get("dry")]
    if chat:
        rows = [d for d in rows if str(d.get("chat")) == str(chat)]
    if not rows:
        print("в личке пусто" + (f" (чат {chat})" if chat else ""))
        return
    for d in rows[-LIMIT:]:
        when = d.get("at", "")[:16].replace("T", " ")
        if d.get("dir") == "in":
            print(f"[{when}] {d.get('name', '?')} (чат {d.get('chat')}): {d.get('text', '')}")
        else:
            print(f"[{when}] я → чат {d.get('chat')}: {d.get('text', '')}")


def main():
    a = sys.argv[1:]
    if a and a[0] == "list":
        listing(a[1] if len(a) > 1 and a[1].strip() else None)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
