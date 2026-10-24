#!/usr/bin/env python3
"""Ответить человеку в личку Telegram.

    reply.py <номер чата> "<текст>"

Номер чата приходит вместе с сообщением. Переписка ложится в state/dialogs.jsonl.
"""
import json, sys, urllib.error, urllib.request
from _common import DRY, STATE, append_jsonl, now, secrets

MAX_LEN = 4096                                       # предел Telegram на одно сообщение


def refuse(msg):
    print(f"не отправлено: {msg}")
    sys.exit(1)

def main():
    if len(sys.argv) < 3 or not sys.argv[2].strip():
        refuse('использование: reply.py <номер чата> "<текст>"')
    chat, text = sys.argv[1].strip(), sys.argv[2].strip()
    if len(text) > MAX_LEN:
        refuse(f"текст длиннее {MAX_LEN} символов ({len(text)}): раздели на два сообщения")
    t = now().isoformat(timespec="seconds")
    if DRY:
        append_jsonl(STATE / "dialogs.jsonl", {"at": t, "dir": "out", "chat": chat, "text": text, "dry": True})
        print(f"[сухой прогон] в личку {chat} не отправлено: {text[:80]}…")
        return
    token = secrets().get("TELEGRAM_BOT_TOKEN")
    if not token:
        refuse("нет ключа бота — запусти tools/keys.py")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", method="POST",
                                 data=json.dumps({"chat_id": chat, "text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        refuse(f"Telegram ответил {e.code}: {json.loads(e.read().decode() or '{}').get('description', '')}")
    except urllib.error.URLError as e:
        refuse(f"нет связи с Telegram: {e.reason}")
    append_jsonl(STATE / "dialogs.jsonl", {"at": t, "dir": "out", "chat": chat, "text": text,
                                           "message_id": res["result"]["message_id"]})
    print(f"отправлено в личку {chat}")


if __name__ == "__main__":
    main()
