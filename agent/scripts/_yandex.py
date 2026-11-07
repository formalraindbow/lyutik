#!/usr/bin/env python3
"""Яндекс Музыка для music.py. Запускается окружением слуха (EARS_PYTHON), где стоит yandex-music.

    _yandex.py search "<запрос>"
    _yandex.py get <id трека> <путь.mp3>

Токен — YANDEX_MUSIC_TOKEN в secrets.env. search печатает строки «id\tназвание\tисполнители\tсекунды»,
get скачивает mp3 и печатает «название\tисполнители\tсекунды».
"""
import sys
from pathlib import Path


def token():
    p = Path.home() / ".config" / "lyutik" / "secrets.env"
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("YANDEX_MUSIC_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    print("нет YANDEX_MUSIC_TOKEN в secrets.env", file=sys.stderr)
    sys.exit(2)

def client():
    from yandex_music import Client
    return Client(token()).init()

def who(track):
    return ", ".join(a.name for a in (track.artists or []) if a and a.name) or "?"

def main():
    a = sys.argv[1:]
    if a[:1] == ["search"] and len(a) >= 2:
        res = client().search(a[1], type_="track")
        for t in ((res.tracks.results if res and res.tracks else []) or [])[:8]:
            print(f"{t.track_id}\t{t.title}\t{who(t)}\t{(t.duration_ms or 0) // 1000}")
    elif a[:1] == ["get"] and len(a) >= 3:
        c = client()
        tr = c.tracks([a[1]])
        if not tr:
            print("нет такого трека", file=sys.stderr)
            sys.exit(3)
        t = tr[0]
        if not t.available:
            print("трек недоступен для скачивания", file=sys.stderr)
            sys.exit(4)
        t.download(a[2], codec="mp3", bitrate_in_kbps=192)
        print(f"{t.title}\t{who(t)}\t{(t.duration_ms or 0) // 1000}")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
