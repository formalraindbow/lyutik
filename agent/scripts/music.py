#!/usr/bin/env python3
"""Чужая музыка: найти, скачать в свою библиотеку, вспомнить, что там есть.

    music.py search "<запрос>" [yandex|soundcloud|youtube]
    music.py get <ссылка>
    music.py library
    music.py chart [сколько]
    music.py lyrics <yandex:номер или ссылка на трек Яндекс Музыки>

search — до восьми находок: название, исполнитель или канал, длина, ссылка. Где искать по умолчанию — MUSIC_DEFAULT
(на сервере yandex: Яндекс Музыка по подписке, токен YANDEX_MUSIC_TOKEN; YouTube из дата-центра скачивать не даёт).
get — звук по ссылке в agent/lyutik/media/library/ (до 12 минут), список — state/library.jsonl. Послушать — listen с путём.
library — что уже лежит в библиотеке.
chart — чарт Яндекс Музыки: что слушают прямо сейчас, по умолчанию 20 треков.
lyrics — текст песни с Яндекс Музыки, дословно: цитировать можно без ошибок слуха.
Поиск и скачивание идут через yt-dlp из окружения слуха (EARS_PYTHON в secrets.env).
"""
import json, os, re, subprocess, sys
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

def ears_python():
    return secrets().get("EARS_PYTHON") or str(Path.home() / ".local" / "share" / "lyutik" / "ears" / "bin" / "python")

def yandex(*args, timeout=120):
    r = subprocess.run([ears_python(), str(Path(__file__).with_name("_yandex.py")), *args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        fail(f"Яндекс Музыка: {(r.stderr or r.stdout).strip()[-240:]}")
    return [l.split("\t") for l in r.stdout.splitlines() if "\t" in l]

def search(query, where="youtube"):
    if where.lower() == "yandex":
        rows = yandex("search", query)
        if not rows:
            fail(f"по «{query}» на Яндекс Музыке ничего")
        print(f"по «{query}» (Яндекс Музыка):")
        for i, (tid, title, artists, sec) in enumerate(rows, 1):
            print(f"[{i}] {title} — {artists} · {clock(sec)}\n    yandex:{tid}")
        return
    prefix = {"youtube": "ytsearch8", "soundcloud": "scsearch8"}.get(where.lower())
    if not prefix:
        fail("где искать — yandex, soundcloud или youtube")
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

def get_yandex(tid, url):
    have = next((x for x in read_jsonl(LIB) if x.get("url") == url), None)
    if have and (ROOT / have["path"]).exists():
        print(f"уже в библиотеке: {have['path']} — «{have['title']}»")
        return
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    out = LIB_DIR / f"yandex_{tid.replace(':', '_')}.mp3"
    if DRY:
        print(f"[сухой прогон] скачал бы {url} в {out.relative_to(ROOT)}")
        return
    rows = yandex("get", tid, str(out), timeout=300)
    title, artists, sec = (rows[-1] + ["", "", "0"])[:3] if rows else ("?", "?", "0")
    rel = str(out.relative_to(ROOT))
    append_jsonl(LIB, {"at": now().isoformat(timespec="seconds"), "title": title, "channel": artists, "url": url,
                       "path": rel, "seconds": int(sec or 0)})
    print(f"в библиотеке: {rel} — «{title}», {artists}, {clock(sec)}. Послушать — listen с этим путём.")

def get(url):
    m = re.match(r"^yandex:(\d+(?::\d+)?)$", url) or re.search(r"music\.yandex\.\w+/(?:album/\d+/)?track/(\d+)", url)
    if m:
        return get_yandex(m.group(1), url if url.startswith("yandex:") else f"yandex:{m.group(1)}")
    if not re.match(r"^https?://", url):
        fail("нужна ссылка http(s)://… или yandex:<номер> из поиска")
    have = next((x for x in read_jsonl(LIB) if x.get("url") == url), None)
    if have and (ROOT / have["path"]).exists():
        print(f"уже в библиотеке: {have['path']} — «{have['title']}»")
        return
    meta = subprocess.run([ytdlp(), "--print", "%(title)s\t%(channel,uploader)s\t%(duration)s", "--skip-download", "--", url],
                          capture_output=True, text=True, timeout=90)
    parts = (meta.stdout.strip().splitlines() or [""])[-1].split("\t")
    if len(parts) < 3:
        if re.search(r"(?i)sign in|not a bot|cookies", meta.stderr):
            fail("YouTube с этого сервера скачивать не даёт — найди то же на soundcloud (music_search с where=soundcloud)")
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
        if re.search(r"(?i)sign in|not a bot|cookies", r.stderr + meta.stderr):
            fail("YouTube с этого сервера скачивать не даёт — найди то же на soundcloud (music_search с where=soundcloud)")
        fail(f"не скачалось: {r.stderr.strip()[-240:]}")
    rel = str(out.relative_to(ROOT))
    append_jsonl(LIB, {"at": now().isoformat(timespec="seconds"), "title": title, "channel": ch, "url": url,
                       "path": rel, "seconds": int(float(dur)) if dur.replace(".", "").isdigit() else None})
    print(f"в библиотеке: {rel} — «{title}», {ch}, {clock(dur)}. Послушать — listen с этим путём.")

def chart(n=20):
    rows = yandex("chart", str(n))
    if not rows:
        fail("Яндекс Музыка не отдала чарт")
    print("чарт Яндекс Музыки — что слушают прямо сейчас:")
    for pos, tid, title, artists, sec in rows:
        print(f"[{pos}] {title} — {artists} · {clock(sec)}\n    yandex:{tid}")

def lyrics(ref):
    m = re.match(r"^yandex:(\d+(?::\d+)?)$", ref) or re.search(r"music\.yandex\.\w+/(?:album/\d+/)?track/(\d+)", ref)
    if not m:
        fail("текст есть у треков Яндекс Музыки: yandex:<номер> из music_search или music_chart")
    r = subprocess.run([ears_python(), str(Path(__file__).with_name("_yandex.py")), "lyrics", m.group(1)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        fail(f"Яндекс Музыка: {(r.stderr or r.stdout).strip()[-240:]}")
    head, _, text = r.stdout.partition("\n")
    title, artists = (head.split("\t") + ["?", "?"])[:2]
    print(f"«{title}» — {artists}, текст с Яндекс Музыки:\n\n{text.strip()}\n\n{tally(text)}")

STOP = set("тебя меня тебе мной тобой этот этого этой эти когда только чтобы если будто потому такой такая такие "
           "всего всех есть него нему нами вами свой своя свои своих тоже даже было были будет буду просто очень "
           "зачем вдруг опять снова сейчас тогда туда сюда здесь где-то".split())

def tally(text):
    """Счёт повторов кодом: на глаз модель врёт в числах (проба 8.11: «растоплю» 14 вместо 12, «навсегда» 12 вместо 17)."""
    lines = [l.strip().lower() for l in text.splitlines() if l.strip()]
    words = re.findall(r"[а-яёa-z]+(?:-[а-яёa-z]+)?", text.lower())
    freq = {}
    for w in words:
        if len(w) >= 4 and w not in STOP:
            freq[w] = freq.get(w, 0) + 1
    top = sorted(((n, w) for w, n in freq.items() if n >= 3), reverse=True)[:8]
    rep = {}
    for l in lines:
        rep[l] = rep.get(l, 0) + 1
    again = sorted(((n, l) for l, n in rep.items() if n >= 3), reverse=True)[:4]
    out = [f"Счёт по тексту (считал код — эти числа верные): слов {len(words)}, строк {len(lines)}, "
           f"разных строк {len(rep)}."]
    if top:
        out.append("Чаще всего: " + ", ".join(f"«{w}» ×{n}" for n, w in top) + ".")
    if again:
        out.append("Строки по кругу: " + "; ".join(f"«{l}» ×{n}" for n, l in again) + ".")
    return "\n".join(out)

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
        search(a[1], a[2] if len(a) > 2 and a[2] else os.environ.get("MUSIC_DEFAULT", "youtube"))
    elif a[:1] == ["get"] and len(a) >= 2:
        get(a[1].strip())
    elif a[:1] == ["library"]:
        library()
    elif a[:1] == ["chart"]:
        chart(a[1] if len(a) > 1 and a[1].isdigit() else 20)
    elif a[:1] == ["lyrics"] and len(a) >= 2:
        lyrics(a[1].strip())
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
