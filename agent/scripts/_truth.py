"""Книга учёта: что люди на самом деле сказали, что он сам опубликовал и что прочитал — по журналам, не по памяти.
Цитата, которой в журналах нет, — «по памяти»: может, и верная, но следа у неё нет.
"""
import json, re
from pathlib import Path
from _common import ROOT, STATE, read_jsonl

EVENTS = ROOT / "logs" / "events.jsonl"
CANON = Path.home() / ".local" / "share" / "lyutik" / "canon.jsonl"   # книги о его мире, тот же корпус, что у canon.py
MIN_CHARS = 12                                         # короче — не цитата, а слово
SOURCES = {"web_read", "canon_read", "canon_search", "comments", "dms", "web_search", "look", "listen"}
# read_file сюда не входит нарочно: иначе его собственные заметки подтверждали бы его же выдумки


def norm(s):
    s = str(s).lower().replace("ё", "е")
    s = re.sub(r"[^\w\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def ledger():
    """Всё, что прозвучало и было прочитано, одной нормализованной строкой."""
    parts = []
    for p in (STATE / "dialogs.jsonl", STATE / "posts.jsonl"):
        parts += [str(r.get("text", "")) for r in read_jsonl(p)]
    if EVENTS.exists():
        for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("kind") == "tool" and e.get("name") in SOURCES:
                parts.append(str(e.get("result", "")))
            elif e.get("kind") in ("dm", "comment"):
                parts.append(str(e.get("text", "")))
    if CANON.exists():                                 # книги целиком: цитата из саги — дословная, читал он её или нет
        for line in CANON.open(encoding="utf-8"):
            try:
                parts.append(json.loads(line).get("text", ""))
            except ValueError:
                pass
    return norm("\n".join(parts))

def quotes(text):
    """Куски в кавычках; многоточие делит цитату на части, каждая проверяется отдельно."""
    out = []
    for q in re.findall(r"«([^«»]+)»", text) + re.findall(r"\"([^\"\n]+)\"", text):
        for piece in re.split(r"…|\.\.\.", q):
            if len(norm(piece)) >= MIN_CHARS:
                out.append(piece.strip())
    return out

def check(text, book=None):
    """(дословные, без следа) для цитат из text."""
    book = ledger() if book is None else book
    found, missing = [], []
    for piece in quotes(text):
        (found if norm(piece) in book else missing).append(piece)
    return found, missing

def mark(text, missing):
    """Пометить в тексте цитаты без следа: «…» → «…» (по памяти)."""
    for piece in missing:
        for wrap in ("«{}»", '"{}"'):
            text = text.replace(wrap.format(piece), wrap.format(piece) + " (по памяти)", 1) \
                if wrap.format(piece) in text else text
    return text
