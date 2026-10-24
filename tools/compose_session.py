#!/usr/bin/env python3
"""Собрать промпт одной сессии персонажа.

    python3 tools/compose_session.py lyutik
    python3 tools/compose_session.py lyutik --from-operator "текст"

По образцу tools/compose_session.py из 3ndetz/agent-workplace (MIT, docs/agent-workplace-LICENSE).
Печатает промпт в stdout: его видно глазами, и два прогона можно сравнить диффом.

Здесь нет ни одного правила про конкретного персонажа: поведение живёт в файлах. Порядок — от
устойчивого к свежему, как советует конспект пары 3: правила и личность в начале, события сессии
в конце. Так повторяющееся начало дешевле обрабатывается, а свежее стоит ближе всего к решению.
"""
import argparse, datetime, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agent"
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
RECENT_POSTS = 5


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""

def posts(home, include_dry=False):
    out = []
    p = home / "state" / "posts.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if (rec.get("dry") and not include_dry) or rec.get("deleted_at"):
                continue
            out.append(rec)
    return out

def ago(delta):
    m = int(delta.total_seconds() // 60)
    if m < 1:
        return "только что"
    if m < 60:
        return f"{m} мин"
    if m < 48 * 60:
        return f"{m // 60} ч {m % 60:02d} мин"
    return f"{m // 1440} дн."

def when(s):
    try:
        return datetime.datetime.fromisoformat(str(s)[:19])
    except ValueError:
        return None

def vitals(home, now):
    """Приборы: что он видит о себе и о зале. Считается из журналов, а не из его памяти — поэтому не врёт."""
    rows = []
    mine = [p for p in posts(home) if p.get("link")]
    if mine:
        times = [(when(p["at"]), p) for p in mine if when(p["at"])]
        day = [p for t, p in times if now - t < datetime.timedelta(hours=24)]
        hour = [p for t, p in times if now - t < datetime.timedelta(hours=1)]
        last_t, last = times[-1]
        rows.append(f"Посты: всего {len(mine)}, за сутки {len(day)}, за час {len(hour)}; последний — {ago(now - last_t)} назад, "
                    f"{len(last.get('text', ''))} знаков" + (f"; средняя длина за сутки {sum(len(p.get('text', '')) for p in day) // len(day)}" if day else "") + ".")
    dialogs = []
    d = home / "state" / "dialogs.jsonl"
    if d.exists():
        for line in d.read_text(encoding="utf-8").splitlines():
            try:
                dialogs.append(json.loads(line))
            except ValueError:
                pass
    dialogs = [x for x in dialogs if not x.get("dry")]
    gone = {x.get("message_id") for x in dialogs if x.get("kind") == "moderation" and x.get("action") == "delete"}
    owner = {x.get("message_id"): x.get("name", "?") for x in dialogs if x.get("dir") == "in" and x.get("kind") == "comment"}
    last_said, last_answered = {}, {}                 # по человеку: когда он писал последний раз и когда я ему отвечал
    for x in dialogs:
        t = when(x.get("at"))
        if not t or x.get("kind") not in ("comment",):
            continue
        if x.get("dir") == "in" and x.get("message_id") not in gone:
            last_said[x.get("name", "?")] = (t, x.get("message_id"), x.get("text", ""))
        elif x.get("dir") == "out" and x.get("reply_to") in owner:
            last_answered[owner[x["reply_to"]]] = t
    waiting = {}
    for name, (t, mid, text) in last_said.items():
        if now - t < datetime.timedelta(hours=72) and (name not in last_answered or last_answered[name] < t):
            waiting[name] = (t, mid, text)
    last_in, last_out = {}, {}
    for x in dialogs:
        t = when(x.get("at"))
        if not t or x.get("kind") != "dm":
            continue
        if x.get("dir") == "in":
            last_in[str(x.get("chat"))] = (t, x.get("name", ""), x.get("text", ""))
        else:
            last_out[str(x.get("chat"))] = t
    for chat, (t, name, text) in last_in.items():
        if now - t < datetime.timedelta(hours=72) and (chat not in last_out or last_out[chat] < t):
            waiting.setdefault(f"{name} (личка, чат {chat})", (t, None, text))
    if waiting:
        items = sorted(waiting.items(), key=lambda kv: kv[1][0])
        rows.append("Ждут ответа: " + "; ".join(
            f"{name}" + (f" (№{mid})" if mid else "") + f" — {ago(now - t)}: «{' '.join(str(text).split())[:90]}»"
            for name, (t, mid, text) in items) + ". Перечитать: comments / dms.")
    else:
        rows.append("Ждут ответа: никто.")
    return rows

def entities(home):
    d = home / "memory" / "entities"
    rows = []
    for f in sorted(d.glob("*.md")) if d.exists() else []:
        events = sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.startswith("- "))
        rows.append(f"- {f.relative_to(ROOT)} — строк: {events}")
    return rows

def compose(name, from_operator=None, wake=None, dry=False):
    home = AGENTS / name
    if not home.is_dir():
        print(f"нет такого персонажа: {name}", file=sys.stderr)
        return None
    L = []
    add = L.append

    add("=== ПРАВИЛА ===\n")
    add(read(AGENTS / "AGENTS.md"))
    add("")
    add(read(AGENTS / "shared" / "rules.md"))
    add("\n=== КТО ТЫ ===\n")
    add(read(home / "PERSONA.md") or "(нет PERSONA.md)")
    add("\n=== ЭТА СЕССИЯ ===\n")
    add(read(home / "SESSION_PROMPT.md") or "(нет SESSION_PROMPT.md)")

    goals = read(home / "GOALS.md")
    if goals:
        add("\n=== ЧТО ТЫ ДЕЛАЕШЬ ===\n")
        add(goals)
    state = read(home / "memory" / "state.md")
    if state:
        add("\n=== ГДЕ ТЫ ОСТАНОВИЛСЯ ===\n")
        add(state)

    add("\n=== ПАМЯТЬ ===\n")
    rows = entities(home)
    add("Заметки о людях, местах и событиях — открывай нужные через read_file:" if rows
        else "Заметок пока нет: память пустая.")
    L.extend(rows)

    mine = posts(home, include_dry=dry)
    add("\n=== ТВОИ ПОСТЫ ===\n")
    if mine:
        add(f"Последние {min(len(mine), RECENT_POSTS)} из {len(mine)}, новые внизу:")
        for p in mine[-RECENT_POSTS:]:
            text = " ".join(p.get("text", "").split())
            add(f"- {p['at'][:16].replace('T', ' ')} · {p.get('link', 'сухой прогон')} · {text[:300]}")
    else:
        add("В канале пока ни одного поста.")

    now = datetime.datetime.now()
    add("\n=== СЕЙЧАС ===\n")
    add(f"{now:%Y-%m-%d %H:%M}, {DAYS[now.weekday()]}.")
    if wake:
        add(wake)
    L.extend(vitals(home, now))

    if from_operator:
        add("\n=== НОВОЕ ОТ ОПЕРАТОРА ===\n")
        add(from_operator)
        add("\nПриходит один раз. Первым делом запиши в GOALS.md пунктом 0 с пометкой OPEN.")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("character")
    ap.add_argument("--from-operator", default=None, help="сообщение оператора, приходит один раз")
    a = ap.parse_args()
    out = compose(a.character, a.from_operator)
    if out is None:
        return 2
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
