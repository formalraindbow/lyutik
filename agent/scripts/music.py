#!/usr/bin/env python3
"""Чужая музыка: найти, скачать в свою библиотеку, вспомнить, что там есть.

    music.py search "<запрос>" [youtube|soundcloud]
    music.py get <ссылка>
    music.py library

search — до восьми находок: название, канал, длина, просмотры, ссылка. По умолчанию YouTube.
get — звук по ссылке в agent/lyutik/media/library/ (до 12 минут), список — state/library.jsonl. Послушать — listen с путём.
library — что уже лежит в библиотеке.
Поиск и скачивание идут через yt-dlp из окружения слуха (EARS_PYTHON в secrets.env).
"""
import json, re, subprocess, sys
from pathlib import Path
from _common import DRY, HOME, ROOT, STATE, append_jsonl, now, read_jsonl, secrets

LIB_DIR = HOME / "media" / "library"
LIB = STATE / "library.jsonl"
MAX_SECONDS = 12 * 60


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def ytdlp():
    py = secrets().get("EARS_PYTHON") or str(Path.home() / ".local" / "share" / "lyutik" / "ears" / "bin" / "python")
    exe = Path(py).parent / "yt-dlp"
    if not exe.exists():
        fail(f"нет yt-dlp рядом с {py} (EARS_PYTHON в secrets.env)")
    return str(exe)

def clock(sec):
    sec = int(float(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"

def search(query, where="youtube"):
    prefix = {"youtube": "ytsearch8", "soundcloud": "scsearch8"}.get(where.lower())
    if not prefix:
        fail("где искать — youtube или soundcloud")
    r = subprocess.run([ytdlp(), f"{prefix}:{query}", "--flat-playlist", "--print",
                        "%(title)s\t%(channel,uploader)s\t%(duration)s\t%(view_count)s\t%(url)s"],
                       capture_output=True, text=True, timeout=90)
    rows = [l.split("\t") for l in r.stdout.splitlines() if l.count("\t") >= 4]
    if not rows:
        fail(f"по «{query}» ничего: {r.stderr.strip()[-200:]}")
    print(f"по «{query}» ({where}):")
    for i, (title, ch, dur, views, url) in enumerate(rows, 1):
        v = f", {int(views):,} просм.".replace(",", " ") if views.isdigit() else ""
        print(f"[{i}] {title} — {ch} · {clock(dur) if dur not in ('NA', '') else '?'}{v}\n    {url}")

def get(url):
    if not re.match(r"^https?://", url):
        fail("нужна ссылка http(s)://…")
    have = next((x for x in read_jsonl(LIB) if x.get("url") == url), None)
    if have and (ROOT / have["path"]).exists():
        print(f"уже в библиотеке: {have['path']} — «{have['title']}»")
        return
    meta = subprocess.run([ytdlp(), "--print", "%(title)s\t%(channel,uploader)s\t%(duration)s", "--skip-download", "--", url],
                          capture_output=True, text=True, timeout=90)
    parts = (meta.stdout.strip().splitlines() or [""])[-1].split("\t")
    if len(parts) < 3:
        fail(f"не открылась ссылка: {meta.stderr.strip()[-200:]}")
    title, ch, dur = parts[0], parts[1], parts[2]
    if dur.replace(".", "").isdigit() and float(dur) > MAX_SECONDS:
        fail(f"длиннее {MAX_SECONDS // 60} минут ({clock(dur)}) — найди запись короче")
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", f"{ch} {title}".lower(), flags=re.U).strip("_")[:60] or "трек"
    out = LIB_DIR / f"{slug}.mp3"
    if DRY:
        print(f"[сухой прогон] скачал бы «{title}» — {ch} в {out.relative_to(ROOT)}")
        return
    r = subprocess.run([ytdlp(), "-q", "-f", "bestaudio[ext=m4a]/bestaudio", "-x", "--audio-format", "mp3",
                        "--audio-quality", "5", "-o", str(out.with_suffix(".%(ext)s")), "--", url],
                       capture_output=True, text=True, timeout=300)
    if not out.exists():
        fail(f"не скачалось: {r.stderr.strip()[-240:]}")
    rel = str(out.relative_to(ROOT))
    append_jsonl(LIB, {"at": now().isoformat(timespec="seconds"), "title": title, "channel": ch, "url": url,
                       "path": rel, "seconds": int(float(dur)) if dur.replace(".", "").isdigit() else None})
    print(f"в библиотеке: {rel} — «{title}», {ch}, {clock(dur)}. Послушать — listen с этим путём.")

def library():
    rows = [x for x in read_jsonl(LIB) if (ROOT / x.get("path", "")).exists()]
    if not rows:
        print("библиотека пуста: найди музыку через music_search и скачай через music_get")
        return
    print(f"в библиотеке {len(rows)}:")
    for x in rows[-40:]:
        print(f"— «{x['title']}», {x.get('channel') or '?'}, {clock(x.get('seconds'))} · {x['path']}")


def main():
    a = sys.argv[1:]
    if a[:1] == ["search"] and len(a) >= 2:
        search(a[1], a[2] if len(a) > 2 and a[2] else "youtube")
    elif a[:1] == ["get"] and len(a) >= 2:
        get(a[1].strip())
    elif a[:1] == ["library"]:
        library()
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
