#!/usr/bin/env python3
"""Комментарии под постами канала: ответить, перечитать, удалить, забанить.

    comment.py reply <номер комментария> "<текст>"    ответ в обсуждении, на конкретный комментарий
    comment.py list [номер поста]                     что уже говорили под постом (или последние)
    comment.py delete <номер комментария>             удалить чужой комментарий из обсуждения
    comment.py ban <номер комментария> [минут]        выгнать автора комментария из обсуждения: на срок или навсегда
    comment.py unban <номер комментария>              вернуть автору комментария право писать

Комментарии к каналу живут в привязанной группе обсуждения. Бот видит их, если он там администратор; удалять
и банить может, если у него есть права на это. Всё услышанное, сказанное и сделанное ложится в state/dialogs.jsonl.
"""
import json, sys, time, urllib.error, urllib.request
from _common import DRY, STATE, append_jsonl, now, read_jsonl, secrets

MAX_LEN, LIST_LIMIT = 4096, 15
DIALOGS = STATE / "dialogs.jsonl"


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

def reply(comment_id, text):
    if not text.strip():
        fail('пустой текст: comment.py reply <номер комментария> "<текст>"')
    if len(text) > MAX_LEN:
        fail(f"текст длиннее {MAX_LEN} символов ({len(text)}): раздели на два")
    s = secrets()
    token, channel = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if DRY:
        append_jsonl(DIALOGS, {"at": now().isoformat(timespec="seconds"), "dir": "out", "kind": "comment",
                               "reply_to": comment_id, "text": text, "dry": True})
        print(f"[сухой прогон] ответ на комментарий {comment_id} не отправлен")
        return
    group = api(token, "getChat", chat_id=channel).get("linked_chat_id")
    if not group:
        fail("у канала нет группы обсуждения — комментарии выключены")
    msg = api(token, "sendMessage", chat_id=group, text=text,
              reply_parameters={"message_id": int(comment_id), "allow_sending_without_reply": True})
    post = next((d.get("post") for d in read_jsonl(DIALOGS)
                 if d.get("kind") == "comment" and d.get("message_id") == int(comment_id)), None)
    append_jsonl(DIALOGS, {"at": now().isoformat(timespec="seconds"), "dir": "out", "kind": "comment", "post": post,
                           "chat": group, "reply_to": int(comment_id), "message_id": msg["message_id"], "text": text})
    print(f"ответил в комментариях на №{comment_id}")

def keys():
    s = secrets()
    token, channel = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not channel:
        fail("нет ключей бота или канала — запусти tools/keys.py")
    group = api(token, "getChat", chat_id=channel).get("linked_chat_id")
    if not group:
        fail("у канала нет группы обсуждения — комментарии выключены")
    return token, group

def find(comment_id):
    """Чужой комментарий по номеру — из моей памяти о переписке."""
    rows = [d for d in read_jsonl(DIALOGS) if d.get("kind") == "comment" and d.get("dir") == "in"
            and d.get("message_id") == comment_id]
    return rows[-1] if rows else None

def note(action, **extra):
    append_jsonl(DIALOGS, {"at": now().isoformat(timespec="seconds"), "dir": "out", "kind": "moderation",
                           "action": action, **extra})

def delete(comment_id):
    rec = find(comment_id)
    who = f" ({rec['name']})" if rec else ""
    if DRY:
        print(f"[сухой прогон] комментарий №{comment_id}{who} не удалён")
        return
    token, group = keys()
    api(token, "deleteMessage", chat_id=group, message_id=comment_id)
    note("delete", message_id=comment_id, chat=group, name=rec.get("name", "") if rec else "")
    print(f"комментарий №{comment_id}{who} удалён")

def ban(comment_id, minutes):
    rec = find(comment_id)
    if not rec:
        fail(f"не знаю, чей это комментарий №{comment_id}: его нет в моей памяти")
    uid = rec.get("user_id")
    if not uid:
        fail(f"комментарий №{comment_id} старше моей новой памяти — в нём нет номера человека; "
             f"бан — по его свежему комментарию")
    term = f"на {minutes} мин" if minutes else "навсегда"
    if DRY:
        print(f"[сухой прогон] {rec['name']} не забанен ({term})")
        return
    token, group = keys()
    params = {"chat_id": group, "user_id": uid}
    if minutes:
        params["until_date"] = int(time.time()) + minutes * 60
    api(token, "banChatMember", **params)
    note("ban", user_id=uid, name=rec["name"], username=rec.get("username", ""), minutes=minutes, message_id=comment_id)
    print(f"{rec['name']} выгнан из обсуждения {term}; вернуть — unban {comment_id}")

def unban(comment_id):
    rec = find(comment_id)
    if not rec or not rec.get("user_id"):
        fail(f"не знаю, чей это комментарий №{comment_id}")
    if DRY:
        print(f"[сухой прогон] {rec['name']} не разбанен")
        return
    token, group = keys()
    api(token, "unbanChatMember", chat_id=group, user_id=rec["user_id"], only_if_banned=True)
    note("unban", user_id=rec["user_id"], name=rec["name"], message_id=comment_id)
    print(f"{rec['name']} снова может писать в обсуждении")

def listing(post):
    everything = [d for d in read_jsonl(DIALOGS) if not d.get("dry")]
    deleted = {d.get("message_id") for d in everything if d.get("kind") == "moderation" and d.get("action") == "delete"}
    banned = set()
    for d in everything:                             # последнее действие по человеку решает
        if d.get("kind") == "moderation" and d.get("action") == "ban":
            banned.add(d.get("user_id"))
        elif d.get("kind") == "moderation" and d.get("action") == "unban":
            banned.discard(d.get("user_id"))
    rows = [d for d in everything if d.get("kind") == "comment"]
    if post:
        rows = [d for d in rows if str(d.get("post")) == str(post)]
    if not rows:
        print("комментариев пока нет" + (f" под постом {post}" if post else ""))
        return
    for d in rows[-LIST_LIMIT:]:
        where = f"пост {d['post']}" if d.get("post") else "обсуждение"
        if d["dir"] == "in":
            marks = (" [удалён]" if d["message_id"] in deleted else "") + (" [в бане]" if d.get("user_id") in banned else "")
            print(f"[{d['at'][:16].replace('T', ' ')}] №{d['message_id']} · {where} · {d.get('name', '?')}{marks}: {d['text']}")
        else:
            print(f"[{d['at'][:16].replace('T', ' ')}] я ответил на №{d.get('reply_to')}: {d['text']}")


def main():
    a = sys.argv[1:]
    try:
        if len(a) == 3 and a[0] == "reply":
            reply(int(a[1]), a[2])
        elif a and a[0] == "list":
            listing(a[1] if len(a) > 1 else None)
        elif len(a) == 2 and a[0] == "delete":
            delete(int(a[1]))
        elif a and a[0] == "ban" and len(a) in (2, 3):
            ban(int(a[1]), int(a[2]) if len(a) == 3 and str(a[2]).strip() else 0)
        elif len(a) == 2 and a[0] == "unban":
            unban(int(a[1]))
        else:
            print(__doc__)
            sys.exit(1)
    except ValueError:
        fail("номер комментария и минуты — числа")


if __name__ == "__main__":
    main()
