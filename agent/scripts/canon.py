#!/usr/bin/env python3
"""Книги о мире, откуда пришёл Лютик: поиск и чтение.

    canon.py search "<слова>" [страница]    абзацы, где есть все слова; по 8 на страницу
    canon.py read <номер>                   абзац с соседями, чтобы читать сцену целиком

Слова ищутся началом формы: «лютик» найдёт «Лютика» и «Лютиком». Корпус собирает tools/canon_build.py,
он лежит вне репозитория.
"""
import json, sys
from pathlib import Path

CORPUS = Path.home() / ".local" / "share" / "lyutik" / "canon.jsonl"
PER_PAGE, SNIPPET, AROUND, READ_LIMIT = 8, 360, 6, 4000


def norm(s):
    return s.lower().replace("ё", "е")

def load():
    if not CORPUS.exists():
        print("нет корпуса книг: его собирает разработчик через tools/canon_build.py — тикет ему")
        sys.exit(1)
    return [json.loads(line) for line in CORPUS.open(encoding="utf-8")]

def snippet(text, word):
    i = max(0, norm(text).find(word) - SNIPPET // 3)
    s = text[i:i + SNIPPET]
    return ("…" if i else "") + s + ("…" if i + SNIPPET < len(text) else "")

def search(query, page):
    words = [norm(w) for w in query.split() if w.strip()]
    if not words:
        print('пустой запрос: canon.py search "<слова>"')
        sys.exit(1)
    rows = [r for r in load() if all(w in norm(r["text"]) for w in words)]
    if not rows:
        print(f"по «{query}» ничего: попробуй другое слово или короче основу")
        return
    pages = (len(rows) + PER_PAGE - 1) // PER_PAGE
    page = max(1, min(page, pages))
    print(f"по «{query}» абзацев: {len(rows)}, страница {page} из {pages}")
    for r in rows[(page - 1) * PER_PAGE: page * PER_PAGE]:
        print(f"[{r['id']}] {r['book']} · {r['chapter']}: {snippet(r['text'], words[0])}")

def read(pid):
    rows = load()
    if not 0 <= pid < len(rows):
        print(f"нет абзаца {pid}: номера от 0 до {len(rows) - 1}")
        sys.exit(1)
    head = rows[pid]
    part = [r for r in rows[max(0, pid - AROUND): pid + AROUND + 1] if r["book"] == head["book"]]
    out = f"{head['book']} · {head['chapter']} · абзацы {part[0]['id']}–{part[-1]['id']}\n"
    for r in part:
        out += ("» " if r["id"] == pid else "") + r["text"] + "\n"
    print(out[:READ_LIMIT] + ("\n…(обрезано)" if len(out) > READ_LIMIT else ""))


def main():
    a = sys.argv[1:]
    try:
        if a and a[0] == "search":
            search(a[1] if len(a) > 1 else "", int(a[2]) if len(a) > 2 else 1)
        elif a and a[0] == "read":
            read(int(a[1]))
        else:
            print(__doc__)
            sys.exit(1)
    except (IndexError, ValueError):
        print('не понял аргументы: canon.py search "<слова>" [страница] | canon.py read <номер>')
        sys.exit(1)


if __name__ == "__main__":
    main()
