#!/usr/bin/env python3
"""Свой канал снаружи: как он выглядит и кто в нём.

    channel.py info                       название, описание, сколько подписчиков
    channel.py describe "<текст>"         новое описание канала, до 255 знаков
    channel.py photo <путь к картинке>    новый аватар канала (jpg или png, путь от корня репозитория)

Бот меняет описание и аватар, если он администратор канала с правом менять профиль.
"""
import json, mimetypes, sys, urllib.error, urllib.request, uuid
from pathlib import Path
from _common import DRY, ROOT, secrets

MAX_DESC = 255


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def api(token, method, data=None, files=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:                                            # multipart: так Telegram принимает файлы
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, (name, blob, mime) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n").encode() + blob + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, method="POST", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, method="POST", data=json.dumps(data or {}).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())["result"]
    except urllib.error.HTTPError as e:
        fail(f"Telegram ответил {e.code}: {json.loads(e.read().decode() or '{}').get('description', '')}")
    except urllib.error.URLError as e:
        fail(f"нет связи с Telegram: {e.reason}")

def keys():
    s = secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        fail("нет ключей бота или канала — запусти tools/keys.py")
    return token, chat

def info():
    token, chat = keys()
    ch = api(token, "getChat", {"chat_id": chat})
    n = api(token, "getChatMemberCount", {"chat_id": chat})
    print(f"канал: {ch.get('title', '?')}" + (f" (@{ch['username']})" if ch.get("username") else ""))
    print(f"описание: {ch.get('description') or '(пусто)'}")
    print(f"подписчиков: {n}")
    print("аватар: " + ("есть" if ch.get("photo") else "нет"))

def describe(text):
    text = text.strip()
    if len(text) > MAX_DESC:
        fail(f"описание — до {MAX_DESC} знаков, а тут {len(text)}")
    if DRY:
        print(f"[сухой прогон] описание не изменено: {text[:80]}")
        return
    token, chat = keys()
    api(token, "setChatDescription", {"chat_id": chat, "description": text})
    print("описание канала обновлено" if text else "описание канала снято")

def photo(path):
    p = (ROOT / str(path).strip().lstrip("/")).resolve()
    if ROOT not in p.parents or not p.is_file():
        fail(f"нет такого файла в репозитории: {path}")
    mime = mimetypes.guess_type(p.name)[0] or ""
    if mime not in ("image/jpeg", "image/png"):
        fail("аватар — jpg или png")
    if p.stat().st_size > 5_000_000:
        fail("файл больше 5 МБ")
    if DRY:
        print(f"[сухой прогон] аватар не изменён: {p.name}")
        return
    token, chat = keys()
    api(token, "setChatPhoto", {"chat_id": chat}, files={"photo": (p.name, p.read_bytes(), mime)})
    print(f"аватар канала обновлён: {p.name}")


def main():
    a = sys.argv[1:]
    if a and a[0] == "info":
        info()
    elif len(a) == 2 and a[0] == "describe":
        describe(a[1])
    elif len(a) == 2 and a[0] == "photo":
        photo(a[1])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
