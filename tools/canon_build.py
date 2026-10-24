#!/usr/bin/env python3
"""Собрать корпус канона из fb2 для инструментов canon_search и canon_read.

    python3 tools/canon_build.py "<путь к книге .fb2>"

Пишет ~/.local/share/lyutik/canon.jsonl: абзац на строку — номер, книга, глава, текст. Корпус и сами книги
живут вне репозитория: это чужие тексты, в публичный репозиторий они не идут.
"""
import json, sys
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path.home() / ".local" / "share" / "lyutik" / "canon.jsonl"
NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"


def text(el):
    return " ".join("".join(el.itertext()).split())

def title(sec):
    t = sec.find(f"{NS}title")
    return " ".join(text(p) for p in t.findall(f"{NS}p")) if t is not None else ""

def walk(sec, book, chapter, rows):
    for child in sec:
        if child.tag == f"{NS}section":
            walk(child, book, title(child) or chapter, rows)
        elif child.tag in (f"{NS}p", f"{NS}v", f"{NS}subtitle", f"{NS}text-author"):
            t = text(child)
            if t:
                rows.append({"id": len(rows), "book": book, "chapter": chapter, "text": t})
        elif child.tag in (f"{NS}poem", f"{NS}stanza", f"{NS}cite", f"{NS}epigraph"):
            walk(child, book, chapter, rows)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    body = ET.parse(sys.argv[1]).getroot().find(f"{NS}body")
    rows = []
    for sec in body.findall(f"{NS}section"):
        book = title(sec)
        if book:
            walk(sec, book, book, rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    books = sorted({r["book"] for r in rows}, key=[r["book"] for r in rows].index)
    print(f"абзацев {len(rows)}, книг {len(books)}: {', '.join(books)} → {OUT}")


if __name__ == "__main__":
    main()
