#!/usr/bin/env python3
"""Опубликовать пост в канале или поправить свой старый.

    post.py "<текст поста>"
    post.py edit <номер поста> "<новый текст целиком>"
    post.py delete <номер поста>                          снять свой пост из канала

Текст — Markdown: заголовки #, списки, таблицы, цитаты >, **жирный**, _курсив_, и свёрнутые секции
<details><summary>заголовок</summary>текст</details> — в ленте они закрыты и раскрываются по тапу.
До 32 768 знаков. Если Telegram не примет разметку, пост уйдёт обычным текстом (до 4096), и об этом будет сказано.
Печатает ссылку на пост или понятную ошибку. Каждый пост записывается в state/posts.jsonl.
"""
import json, sys, urllib.error, urllib.request
from _common import DRY, STATE, append_jsonl, now, read_jsonl, secrets, tg_link

MAX_RICH, MAX_PLAIN = 32768, 4096                    # пределы Telegram: с разметкой и без
POSTS = STATE / "posts.jsonl"


class TelegramError(Exception):
    pass

def refuse(msg):
    print(f"не опубликовано: {msg}")
    sys.exit(1)

def api(token, method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", method="POST",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["result"]
    except urllib.error.HTTPError as e:
        raise TelegramError(f"Telegram ответил {e.code}: {json.loads(e.read().decode() or '{}').get('description', '')}")
    except urllib.error.URLError as e:
        raise TelegramError(f"нет связи с Telegram: {e.reason}")

def edit(message_id, text):
    """Поправить свой пост: Telegram меняет текст на месте, журнал запоминает новую версию и время правки."""
    if len(text) > MAX_RICH:
        refuse(f"текст длиннее {MAX_RICH} знаков ({len(text)})")
    rows = read_jsonl(POSTS)
    rec = next((r for r in rows if r.get("message_id") == message_id), None)
    if not rec:
        refuse(f"поста {message_id} нет в моём журнале — править можно только свои")
    t = now()
    if DRY:
        print(f"[сухой прогон] пост {message_id} не правлен, новый текст {len(text)} знаков")
        return
    s = secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        refuse("нет ключей бота или канала — запусти tools/keys.py")
    fmt, note = "rich", ""
    try:
        api(token, "editMessageText", chat_id=chat, message_id=message_id, rich_message={"markdown": text})
    except TelegramError as e:
        if len(text) > MAX_PLAIN:
            refuse(f"разметку Telegram не принял ({e}), а без неё текст длиннее {MAX_PLAIN} знаков")
        try:
            api(token, "editMessageText", chat_id=chat, message_id=message_id, text=text,
                link_preview_options={"is_disabled": True})
        except TelegramError as e2:
            refuse(str(e2))
        fmt, note = "plain", f" — без разметки: {e}"
    rec.update(text=text, format=fmt, edited_at=t.isoformat(timespec="seconds"))
    POSTS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"пост {message_id} поправлен {t:%d.%m %H:%M}: {rec.get('link', '')}{note}")

def delete(message_id):
    """Снять свой пост из канала. Журнал помнит, что он был и когда снят."""
    rows = read_jsonl(POSTS)
    rec = next((r for r in rows if r.get("message_id") == message_id), None)
    if not rec:
        refuse(f"поста {message_id} нет в моём журнале — снимать можно только свои")
    t = now()
    if DRY:
        print(f"[сухой прогон] пост {message_id} не снят")
        return
    s = secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        refuse("нет ключей бота или канала — запусти tools/keys.py")
    try:
        api(token, "deleteMessage", chat_id=chat, message_id=message_id)
    except TelegramError as e:
        refuse(str(e))
    rec["deleted_at"] = t.isoformat(timespec="seconds")
    POSTS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"пост {message_id} снят из канала {t:%d.%m %H:%M}")

def photo(path, caption):
    """Пост с картинкой: картинка из репозитория (jpg/png), подпись до 1024 знаков."""
    import mimetypes, uuid
    from _common import ROOT
    p = (ROOT / str(path).strip().lstrip("/")).resolve()
    if ROOT not in p.parents or not p.is_file():
        refuse(f"нет такого файла в репозитории: {path}")
    mime = mimetypes.guess_type(p.name)[0] or ""
    if mime not in ("image/jpeg", "image/png"):
        refuse("картинка — jpg или png")
    if len(caption) > 1024:
        refuse(f"подпись к картинке — до 1024 знаков, а тут {len(caption)}; длинное — отдельным постом")
    t = now()
    if DRY:
        append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": "🖼 " + caption, "photo": str(p.relative_to(ROOT)), "dry": True})
        print(f"[сухой прогон] пост с картинкой {p.name} не отправлен")
        return
    s = secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        refuse("нет ключей бота или канала — запусти tools/keys.py")
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in {"chat_id": chat, "caption": caption}.items():
        if v:
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{p.name}\"\r\n"
             f"Content-Type: {mime}\r\n\r\n").encode() + p.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", method="POST", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            msg = json.loads(r.read().decode())["result"]
    except urllib.error.HTTPError as e:
        refuse(f"Telegram ответил {e.code}: {json.loads(e.read().decode() or '{}').get('description', '')}")
    except urllib.error.URLError as e:
        refuse(f"нет связи с Telegram: {e.reason}")
    link = tg_link(msg)
    append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": "🖼 " + caption, "link": link,
                         "message_id": msg["message_id"], "format": "photo", "photo": str(p.relative_to(ROOT))})
    print(f"опубликовано с картинкой {t:%d.%m %H:%M}: {link}")

def main():
    if len(sys.argv) == 4 and sys.argv[1] == "photo":
        photo(sys.argv[2], sys.argv[3].strip())
        return
    if len(sys.argv) == 3 and sys.argv[1] == "delete":
        try:
            delete(int(sys.argv[2]))
        except ValueError:
            refuse("номер поста — число из ссылки: t.me/pollveka/21 → 21")
        return
    if len(sys.argv) == 4 and sys.argv[1] == "edit":
        try:
            edit(int(sys.argv[2]), sys.argv[3].strip())
        except ValueError:
            refuse("номер поста — число из ссылки: t.me/pollveka/21 → 21")
        return
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        refuse('пустой текст. Использование: post.py "<текст>"')
    text = sys.argv[1].strip()
    if len(text) > MAX_RICH:
        refuse(f"текст длиннее {MAX_RICH} знаков ({len(text)}): раздели на два поста")

    t = now()
    if DRY:
        append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": text, "dry": True})
        print(f"[сухой прогон] пост не отправлен, {len(text)} знаков: {text[:80]}…")
        return

    s = secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        refuse("нет ключей бота или канала — запусти tools/keys.py")
    fmt, note = "rich", ""
    try:
        msg = api(token, "sendRichMessage", chat_id=chat, rich_message={"markdown": text})
    except TelegramError as e:                        # разметку не приняли — обычным текстом, но честно
        if len(text) > MAX_PLAIN:
            refuse(f"разметку Telegram не принял ({e}), а без неё текст длиннее {MAX_PLAIN} знаков")
        try:
            msg = api(token, "sendMessage", chat_id=chat, text=text, link_preview_options={"is_disabled": True})
        except TelegramError as e2:
            refuse(str(e2))
        fmt, note = "plain", f" — ушёл без разметки: {e}"
    link = tg_link(msg)
    append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": text, "link": link,
                         "message_id": msg["message_id"], "format": fmt})
    print(f"опубликовано {t:%d.%m %H:%M}: {link}{note}")


if __name__ == "__main__":
    main()
