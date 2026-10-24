#!/usr/bin/env python3
"""Опрос в канале и его итоги.

    poll.py new "<вопрос>" "<вариант>" "<вариант>" ...   опрос в канале: вопрос до 300 знаков, 2–12 вариантов до 100
    poll.py list                                           свои опросы и голоса

Опрос анонимный, один ответ. Голоса считает цикл (tools/run.py): Telegram присылает состояние опроса при каждом
голосе, оно ложится в state/polls.json.
"""
import json, sys, urllib.error, urllib.request
from _common import DRY, STATE, now, secrets, tg_link

POLLS = STATE / "polls.json"


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def api(token, method, **params):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", method="POST",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["result"]
    except urllib.error.HTTPError as e:
        fail(f"Telegram ответил {e.code}: {json.loads(e.read().decode() or '{}').get('description', '')}")
    except urllib.error.URLError as e:
        fail(f"нет связи с Telegram: {e.reason}")

def load():
    return json.loads(POLLS.read_text(encoding="utf-8")) if POLLS.exists() else {}

def save(d):
    POLLS.parent.mkdir(parents=True, exist_ok=True)
    POLLS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

def new(question, options):
    question = question.strip()
    options = [o.strip() for o in options if o.strip()]
    if not 1 <= len(question) <= 300:
        fail("вопрос — от 1 до 300 знаков")
    if not 2 <= len(options) <= 12:
        fail(f"вариантов — от 2 до 12, а дано {len(options)}")
    if any(len(o) > 100 for o in options):
        fail("каждый вариант — до 100 знаков")
    if DRY:
        print(f"[сухой прогон] опрос не отправлен: {question} / {' | '.join(options)}")
        return
    s = secrets()
    token, chat = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not chat:
        fail("нет ключей бота или канала — запусти tools/keys.py")
    msg = api(token, "sendPoll", chat_id=chat, question=question,
              options=[{"text": o} for o in options], is_anonymous=True)
    p, link = msg["poll"], tg_link(msg)
    d = load()
    d[p["id"]] = {"at": now().isoformat(timespec="seconds"), "question": p["question"],
                  "options": [{"text": o["text"], "votes": o.get("voter_count", 0)} for o in p["options"]],
                  "total": p.get("total_voter_count", 0), "closed": p.get("is_closed", False),
                  "message_id": msg["message_id"], "link": link}
    save(d)
    print(f"опрос опубликован {now():%d.%m %H:%M}: {link}")

def listing():
    d = load()
    if not d:
        print("опросов пока не было")
        return
    for p in list(d.values())[-20:]:
        state = "закрыт" if p.get("closed") else "идёт"
        print(f"[{p['at'][:16].replace('T', ' ')}] {p['question']} — {p['link']} · {state}, голосов: {p['total']}")
        for o in p["options"]:
            print(f"   {o['votes']:>3} · {o['text']}")


def main():
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "new":
        new(a[1], a[2:])
    elif a and a[0] == "list":
        listing()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
