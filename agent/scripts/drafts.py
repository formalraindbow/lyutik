"""Мастерская: второй канал, где видна работа над песней — каждый дубль, проверка, правка и решение.

Канал — TELEGRAM_DRAFTS_ID в secrets.env (бот в нём админ). Нет канала — тихо ничего не шлёт: работа над песней
от мастерской не зависит. Подписи — HTML, коротко, без эмодзи.
"""
import html, json, urllib.error, urllib.request, uuid
from pathlib import Path
from _common import DRY, secrets, tg_link


def esc(s):
    return html.escape(str(s), quote=False)

def post(text, audio=None, title="", performer="Лютик · мастерская"):
    """Текст или трек с подписью в канал-мастерскую. Возвращает ссылку или пусто."""
    s = secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_DRAFTS_ID")
    if DRY or not token or not chat:
        return ""
    try:
        if audio:
            b = uuid.uuid4().hex
            fields = {"chat_id": chat, "caption": text[:1000], "parse_mode": "HTML", "title": title[:60],
                      "performer": performer, "disable_notification": "true"}
            body = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                            for k, v in fields.items())
            body += (f"--{b}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{Path(audio).name}\"\r\n"
                     f"Content-Type: audio/mpeg\r\n\r\n").encode() + Path(audio).read_bytes() + f"\r\n--{b}--\r\n".encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendAudio", data=body, method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        else:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", method="POST",
                                         data=json.dumps({"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                                                          "disable_notification": True,
                                                          "link_preview_options": {"is_disabled": True}}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return tg_link(json.loads(r.read().decode())["result"])
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return ""                                          # мастерская не должна ронять работу над песней
