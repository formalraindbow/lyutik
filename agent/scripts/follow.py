#!/usr/bin/env python3
"""Подписки на чужие публичные каналы Telegram: что там появилось нового. Только чтение, без ключей и без отправок.

    follow.py add <@имя или ссылка t.me/…> ["зачем"]   подписаться: канал в список, всё, что в нём уже есть, — виденное
    follow.py remove <имя>                              отписаться
    follow.py list                                      подписки: сколько нового в каждой, до какого поста видено, когда проверял
    follow.py news [имя]                                новые посты по всем подпискам (или по одной); после выдачи они виденные
    follow.py peek <имя> [сколько]                      последние N постов любого публичного канала (по умолчанию 5), виденное
                                                        не трогает; при N=1 пост показывается целиком

Читается открытая страница t.me/s/<имя> — та же, что видна в браузере без входа. Закрытые каналы, группы, боты и люди
так не читаются. Список подписок — state/follows.json; всё, что выдал news, дописывается в state/follows.jsonl.
"""
import datetime, http.client, json, re, sys, urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser
from _common import DRY, STATE, append_jsonl, now

FOLLOWS = STATE / "follows.json"      # подписки: имя → заголовок, зачем, виденное, когда проверял
JOURNAL = STATE / "follows.jsonl"     # что news показал: полный текст, чтобы цитата потом имела след
UA = "Mozilla/5.0 (lyutik; course project)"
TIMEOUT = 30                          # секунд на одну страницу
CUT = 600                             # знаков текста в выдаче
FULL = 4000                           # потолок, когда пост показывается целиком
PAGES = 3                             # страниц по 20 постов за одну проверку канала
SHOW = 20                             # новых постов одного канала за один news; остальное — следующим вызовом
PEEK_DEFAULT = 5
HOSTS = ("t.me", "telegram.me", "telegram.dog")
VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
BLOCKS = {"blockquote", "div", "p", "pre", "ul", "ol", "li", "h1", "h2", "h3", "h4", "tr"}


class Problem(Exception):
    """Канал не прочитался: текст — для человека, по-русски."""


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def channel_name(raw):
    """@имя, имя, t.me/имя, t.me/s/имя, t.me/имя/123 → имя. Ссылка-приглашение (+хэш) — закрытый чат, так не читается."""
    s = raw.strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(.+)$", s, re.I)
    if m:
        s = m.group(1)
        if s.startswith("s/"):
            s = s[2:]
        s = s.split("?")[0].split("#")[0].strip("/").split("/")[0]
    s = s.lstrip("@")
    if s.startswith("+") or s.lower().startswith("joinchat"):
        fail("это ссылка-приглашение в закрытый канал или группу — так не читается, нужен публичный канал с именем")
    if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", s):
        fail(f"«{raw}» не похоже на имя канала: имя — латиница, цифры и подчёркивание, как @pollveka или t.me/pollveka")
    return s.lower()


# ── страница канала ──────────────────────────────────────────────────────────
def new_post(name, num, service):
    return {"name": name, "num": num, "service": service, "date": "", "text": [], "photos": 0, "videos": 0, "round": 0,
            "voice": 0, "audio": 0, "sticker": 0, "files": [], "location": 0, "contact": 0, "poll": None,
            "unsupported": False, "forwarded": False, "forward": "", "reply": None, "preview": ""}

class Page(HTMLParser):
    """Разбор t.me/s/<имя>: шапка канала и посты. Пост — div.tgme_widget_message с data-post="имя/номер", текст —
    div.tgme_widget_message_text, время — time[datetime] под ссылкой с датой, вложения — по классам обёрток."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.description, self.subscribers = "", "", ""
        self.page_title, self.page_description = "", ""           # <title> и описание страницы, если это не канал
        self.profile, self.action, self.extra = "", "", []          # имя профиля, кнопка и подписи на странице t.me/<имя>
        self.has_channel, self.posts = False, []
        self.more_after = self.more_before = None
        self.depth, self.cur, self.post_depth = 0, None, None
        self.cap, self.cap_depth, self.buf = None, 0, []     # что сейчас собираем, на какой глубине, куда
        self.in_date, self.counters = False, []

    def capture(self, kind):
        self.cap, self.cap_depth, self.buf = kind, self.depth, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag in VOID:
            if tag == "br" and self.cap == "text":
                self.buf.append("\n")
            return
        self.depth += 1
        if self.cap == "text" and tag in BLOCKS:
            self.buf.append("\n")
        if "tgme_widget_message" in cls and a.get("data-post"):
            n = a["data-post"].rsplit("/", 1)
            if len(n) == 2 and n[1].isdigit():
                self.cur, self.post_depth = new_post(n[0].lower(), int(n[1]), "service_message" in cls), self.depth
            return
        if "tgme_channel_info" in cls:
            self.has_channel = True
        if self.cap is None:
            if tag == "title":
                self.capture("page_title")
            elif "tgme_page_description" in cls:
                self.capture("page_description")
            elif "tgme_page_title" in cls:
                self.capture("profile")
            elif "tgme_page_action" in cls:
                self.capture("action")
            elif "tgme_page_extra" in cls:
                self.capture("extra")
            elif "tgme_channel_info_header_title" in cls:
                self.capture("title")
            elif "tgme_channel_info_description" in cls:
                self.capture("description")
            elif "tgme_channel_info_counter" in cls:
                self.capture("counter")
        if "tme_messages_more" in cls:
            if (a.get("data-after") or "").isdigit():
                self.more_after = int(a["data-after"])
            if (a.get("data-before") or "").isdigit():
                self.more_before = int(a["data-before"])
        p = self.cur
        if p is None:
            return
        if any(c.startswith("tgme_widget_message_text") for c in cls) and self.cap is None:
            self.capture("text")
        elif "tgme_widget_message_photo_wrap" in cls:
            p["photos"] += 1
        elif "tgme_widget_message_video_player" in cls:
            p["videos"] += 1
        elif "tgme_widget_message_roundvideo_player" in cls:
            p["round"] += 1
        elif "tgme_widget_message_voice" in cls:
            p["voice"] += 1
        elif "tgme_widget_message_audio" in cls:
            p["audio"] += 1
        elif "tgme_widget_message_sticker_wrap" in cls:
            p["sticker"] += 1
        elif "tgme_widget_message_document_wrap" in cls:
            p["files"].append("")
        elif "tgme_widget_message_document_title" in cls and self.cap is None:
            self.capture("file")
        elif "tgme_widget_message_location_wrap" in cls:
            p["location"] += 1
        elif "tgme_widget_message_contact_wrap" in cls:
            p["contact"] += 1
        elif "tgme_widget_message_poll" in cls:
            p["poll"] = {"q": "", "options": []}
        elif "tgme_widget_message_poll_question" in cls and self.cap is None:
            self.capture("poll_q")
        elif "tgme_widget_message_poll_option_text" in cls and self.cap is None:
            self.capture("poll_o")
        elif "message_media_not_supported_label" in cls:
            p["unsupported"] = True
        elif "tgme_widget_message_forwarded_from" in cls:
            p["forwarded"] = True
        elif "tgme_widget_message_forwarded_from_name" in cls and self.cap is None:
            self.capture("forward")
        elif "link_preview_title" in cls and self.cap is None and not p["preview"]:
            self.capture("preview")
        elif tag == "a" and "tgme_widget_message_reply" in cls:
            tail = (a.get("href") or "").rstrip("/").rsplit("/", 1)[-1]
            p["reply"] = int(tail) if tail.isdigit() else p["reply"]
        elif tag == "a" and "tgme_widget_message_date" in cls:
            self.in_date = True
        elif tag == "time" and a.get("datetime") and (self.in_date or not p["date"]):
            p["date"] = a["datetime"]

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.cap is not None and self.depth == self.cap_depth:
            self.finish()
        elif self.cap == "text" and tag in BLOCKS:
            self.buf.append("\n")
        if self.cur is not None and self.depth == self.post_depth:
            self.cur["text"] = clean("".join(self.cur["text"]))
            self.posts.append(self.cur)
            self.cur = None
        if tag == "a":
            self.in_date = False
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.cap is not None:
            self.buf.append(data)

    def finish(self):
        kind, text = self.cap, "".join(self.buf)
        self.cap = None
        p, flat = self.cur, " ".join(text.split())
        if kind == "text" and p is not None:
            p["text"].append(text)
        elif kind == "title":
            self.title = flat
        elif kind == "description":
            self.description = flat
        elif kind == "counter":
            self.counters.append(flat)
            if "subscriber" in flat:
                self.subscribers = flat.split()[0]
        elif kind == "page_title":
            self.page_title = flat
        elif kind == "page_description":
            self.page_description = flat
        elif kind == "profile":
            self.profile = flat
        elif kind == "action":
            self.action = flat
        elif kind == "extra":
            self.extra.append(flat)
        elif p is not None:
            if kind == "file" and p["files"]:
                p["files"][-1] = flat
            elif kind == "poll_q" and p["poll"]:
                p["poll"]["q"] = flat
            elif kind == "poll_o" and p["poll"]:
                p["poll"]["options"].append(flat)
            elif kind == "forward":
                p["forward"] = flat
            elif kind == "preview":
                p["preview"] = flat

def clean(text):
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

def fetch(name, **params):
    """Страница t.me/s/<имя> (с ?after= или ?before=): текст и конечный адрес после переадресаций."""
    url = f"https://t.me/s/{name}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(3_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
            final = r.geturl()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Problem("Telegram просит подождать (429): слишком часто читаю, попробуй позже")
        raise Problem(f"Telegram ответил {e.code}")
    except urllib.error.URLError as e:
        raise Problem(f"нет связи: {e.reason}")
    except (TimeoutError, OSError, http.client.HTTPException, ValueError) as e:
        raise Problem(f"страница не открылась за {TIMEOUT} с: {e}")
    return raw.decode(charset, errors="replace"), final

def what_is_it(name, pg):
    """Страница t.me/<имя> без /s/ — не канал. Кто это, видно по шапке: имя профиля, кнопка, подписи."""
    desc = re.sub(r"@\w+", "", pg.page_description.lower())      # без упоминаний имени: в нём может быть слово channel
    extra = " ".join(pg.extra).lower()
    who = f" ({pg.profile})" if pg.profile else ""
    only = "; читаю только публичные каналы"
    if "Start Bot" in pg.action or pg.page_title.startswith("Telegram: Launch"):
        return f"@{name} — это бот{who}, а не канал{only}"
    if "Join Group" in pg.action or "group" in desc or "member" in extra:
        return f"@{name} — это группа{who}, а не канал{only}"
    if "Join Channel" in pg.action or "channel" in desc:
        return f"@{name} — закрытый канал{who}: читать его можно только изнутри{only}"
    if pg.profile:
        return f"@{name} — это человек{who}, а не канал{only}"
    if not pg.profile and "contact" in desc:
        return f"нет такого канала: @{name} — никого с таким именем в Telegram нет"
    return f"@{name} — это не публичный канал" + (f" ({pg.page_title})" if pg.page_title else "") + only

def page(name, **params):
    """Разобранная страница канала. Несуществующий, закрытый, не канал — Problem с внятным текстом."""
    body, final = fetch(name, **params)
    u = urllib.parse.urlsplit(final)
    host = (u.hostname or "").lower()
    pg = Page()
    pg.feed(body)
    pg.close()
    if host not in HOSTS:
        raise Problem(f"нет такого канала: @{name} (Telegram отправил на {host or 'другой адрес'})")
    if not u.path.startswith("/s/"):
        raise Problem(what_is_it(name, pg))
    if not pg.has_channel:
        extra = pg.page_description or pg.page_title
        raise Problem(f"страница @{name} не разобралась" + (f": {extra[:200]}" if extra else ""))
    return pg

def fresh(name, seen):
    """Посты новее seen, по порядку, не больше PAGES страниц. Возвращает (посты, страница с шапкой, есть ли ещё дальше)."""
    posts, after, first = [], seen, None
    for _ in range(PAGES):
        pg = page(name, after=after)
        first = first or pg
        new = [p for p in pg.posts if p["num"] > after]
        posts += new
        if pg.more_after is None or not new:
            return posts, first, False
        after = max([pg.more_after] + [p["num"] for p in new])
    return posts, first, True


# ── выдача ───────────────────────────────────────────────────────────────────
def when(iso):
    if not iso:
        return "никогда"
    try:
        d = datetime.datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso[:16]
    return f"{d:%d.%m %H:%M}" if d.year == now().year else f"{d:%d.%m.%Y %H:%M}"

def cut(text, limit):
    if len(text) <= limit:
        return text
    head = text[:limit]
    sp = max(head.rfind(" "), head.rfind("\n"))
    if sp > limit * 0.7:
        head = head[:sp]
    return head.rstrip(" ,;:—-.…") + "…"

def times(word, n):
    return f"[{word}]" if n == 1 else f"[{word} ×{n}]"

def render(p, title, limit):
    head = f"@{p['name']}" + (f" «{title}»" if title else "") + f" · №{p['num']} · {when(p['date'])}"
    marks = []
    if p["forward"]:
        marks.append(f"переслано от {p['forward']}")
    elif p["forwarded"]:
        marks.append("переслано")
    if p["reply"]:
        marks.append(f"в ответ на №{p['reply']}")
    if p["photos"]:
        marks.append(times("фото", p["photos"]))
    if p["videos"]:
        marks.append(times("видео", p["videos"]))
    if p["round"]:
        marks.append("[видеосообщение]")
    if p["voice"]:
        marks.append("[голосовое]")
    if p["audio"]:
        marks.append("[аудио]")
    if p["sticker"]:
        marks.append("[стикер]")
    for f in p["files"]:
        marks.append(f"[файл: {f}]" if f else "[файл]")
    if p["location"]:
        marks.append("[геопозиция]")
    if p["contact"]:
        marks.append("[контакт]")
    if p["poll"]:
        marks.append("[опрос] " + p["poll"]["q"] + (" — " + " | ".join(p["poll"]["options"]) if p["poll"]["options"] else ""))
    text = p["text"]
    if p["preview"] and p["preview"] not in text:
        marks.append(f"[ссылка: {p['preview']}]")
    if not text and not marks and p["unsupported"]:
        marks.append("[в вебе не показывается — открой пост в Telegram]")
    body = " ".join(marks)
    if text:
        body += ("\n" if body else "") + cut(text, limit)
    return f"{head}\n{body}\nhttps://t.me/{p['name']}/{p['num']}"


# ── состояние ────────────────────────────────────────────────────────────────
def load():
    if not FOLLOWS.exists():
        return {}
    try:
        return json.loads(FOLLOWS.read_text(encoding="utf-8"))
    except ValueError as e:
        fail(f"испорчен {FOLLOWS.relative_to(STATE.parent)}: {e} — поправь файл руками")

def save(d):
    FOLLOWS.parent.mkdir(parents=True, exist_ok=True)
    tmp = FOLLOWS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FOLLOWS)

def stamp():
    return now().isoformat(timespec="seconds")


# ── команды ──────────────────────────────────────────────────────────────────
def add(raw, why):
    name, why = channel_name(raw), why.strip()
    d = load()
    if name in d:
        rec = d[name]
        print(f"уже подписан на @{name} «{rec['title']}» с {when(rec['added'])}; виденное до №{rec['seen']}, новое — news")
        if why and why != rec.get("why"):
            rec["why"] = why
            if DRY:
                print(f"[сухой прогон] «зачем» не обновил: {why}")
            else:
                save(d)
                print(f"обновил «зачем»: {why}")
        return
    try:
        pg = page(name)
    except Problem as e:
        fail(str(e))
    content = [p for p in pg.posts if not p["service"]]
    seen = max([p["num"] for p in pg.posts], default=0)
    print(f"подписался на @{name} «{pg.title or name}»" + (f", подписчиков: {pg.subscribers}" if pg.subscribers else ""))
    if pg.description:
        print(f"о канале: {cut(pg.description, 200)}")
    if why:
        print(f"зачем: {why}")
    if content:
        print(f"постов на странице: {len(content)}, последний — №{content[-1]['num']} от {when(content[-1]['date'])}")
    else:
        print("постов пока нет")
    print("всё, что уже есть, считаю виденным; новое покажет news")
    if DRY:
        print("[сухой прогон] в список не записал")
        return
    d[name] = {"title": pg.title or name, "why": why, "added": stamp(), "seen": seen, "checked": stamp(), "unread": 0}
    save(d)

def remove(raw):
    name = channel_name(raw)
    d = load()
    if name not in d:
        fail(f"@{name} нет в списке подписок" + ("; есть: " + ", ".join(f"@{k}" for k in d) if d else " — список пуст"))
    rec = d.pop(name)
    if DRY:
        print(f"[сухой прогон] @{name} «{rec['title']}» из списка не убрал")
        return
    save(d)
    print(f"отписался от @{name} «{rec['title']}»; осталось подписок: {len(d)}")

def listing():
    d = load()
    if not d:
        print("подписок нет — follow.py add @имя")
        return
    t0 = stamp()
    print(f"подписок: {len(d)}, проверено {when(t0)}")
    for name, rec in d.items():
        try:
            posts, pg, more = fresh(name, rec["seen"])
            content = [p for p in posts if not p["service"]]
            rec["unread"], rec["checked"] = len(content), t0
            if pg.title:
                rec["title"] = pg.title
            flag = f"НОВОЕ: {len(content)}{'+' if more else ''}" if content else "нового нет"
            line = f"@{name} «{rec['title']}» — {flag} · видено до №{rec['seen']}"
        except Problem as e:
            line = f"@{name} «{rec['title']}» — не прочитал: {e} · видено до №{rec['seen']}, прошлая проверка {when(rec.get('checked'))}"
        line += f" · с {when(rec['added'])}" + (f" · зачем: {rec['why']}" if rec.get("why") else "")
        print(line)
    if DRY:
        print("[сухой прогон] время проверки не записал")
    else:
        save(d)

def news(raw):
    d = load()
    if not d:
        fail("подписок нет — follow.py add @имя")
    names = [channel_name(raw)] if raw.strip() else list(d)
    if raw.strip() and names[0] not in d:
        fail(f"@{names[0]} нет в списке подписок; без подписки можно подглядеть: follow.py peek @{names[0]}")
    t0, out, shown, failed = stamp(), [], [], []
    before = [d[n].get("checked") or d[n].get("added") for n in names]
    for name in names:
        rec = d[name]
        try:
            posts, pg, more = fresh(name, rec["seen"])
        except Problem as e:
            failed.append(name)
            out.append(f"@{name}: не прочитал — {e}")
            continue
        if pg.title:
            rec["title"] = pg.title
        content = [p for p in posts if not p["service"]]
        rest = len(content) - SHOW
        content = content[:SHOW]
        for p in content:
            out.append(render(p, rec["title"], CUT))
            shown.append((name, p))
        if content and (rest > 0 or more):
            rec["seen"] = content[-1]["num"]
            out.append(f"@{name}: показал {len(content)} новых, дальше ещё {rest if rest > 0 else 'есть'} — повтори news")
        elif posts:
            rec["seen"] = max(p["num"] for p in posts)
        rec["checked"], rec["unread"] = t0, 0
    if shown:
        print(f"новых постов: {len(shown)}\n")
        print("\n\n".join(out))
        print("\nвидено: " + ", ".join(f"@{n} до №{d[n]['seen']}" for n in names if n not in failed))
    else:
        if out:
            print("\n".join(out))
        last = max(x for x in before if x) if any(before) else ""
        where = "в остальных " if failed and len(names) > len(failed) else ""
        if len(failed) < len(names):
            print(f"{where}нового нет (прошлая проверка {when(last)}, эта — {when(t0)})")
    if DRY:
        print("[сухой прогон] виденное не записал")
        return
    for name, p in shown:
        append_jsonl(JOURNAL, {"at": t0, "channel": name, "post": p["num"], "date": p["date"], "text": p["text"],
                               "link": f"https://t.me/{name}/{p['num']}"})
    save(d)

def peek(raw, count):
    name = channel_name(raw)
    n = count or PEEK_DEFAULT
    if not 1 <= n <= PAGES * 20:
        fail(f"«сколько» — от 1 до {PAGES * 20}")
    try:
        pg = page(name)
    except Problem as e:
        fail(str(e))
    posts, before, pages, note = [p for p in pg.posts if not p["service"]], pg.more_before, 1, ""
    while len(posts) < n and before is not None and pages < PAGES:
        try:
            older = page(name, before=before)
        except Problem as e:
            note = f"\nстарше не дочитал: {e}"
            break
        posts, before, pages = [p for p in older.posts if not p["service"]] + posts, older.more_before, pages + 1
    title = pg.title or name
    if not posts:
        print(f"@{name} «{title}»: постов пока нет")
        return
    posts = posts[-n:]
    print(f"@{name} «{title}»: " + ("последний пост" if len(posts) == 1 else f"последние {len(posts)}")
          + (f", подписчиков: {pg.subscribers}" if pg.subscribers else "") + "\n")
    print("\n\n".join(render(p, title, FULL if n == 1 else CUT) for p in posts) + note)
    d = load()
    if name in d:
        print(f"\nвиденное не трогал: по-прежнему до №{d[name]['seen']}")


def main():
    a = sys.argv[1:]
    try:
        if a and a[0] == "add" and len(a) in (2, 3):
            add(a[1], a[2] if len(a) == 3 else "")
        elif len(a) == 2 and a[0] == "remove":
            remove(a[1])
        elif a and a[0] == "list" and len(a) == 1:
            listing()
        elif a and a[0] == "news" and len(a) in (1, 2):
            news(a[1] if len(a) == 2 else "")
        elif a and a[0] == "peek" and len(a) in (2, 3):
            peek(a[1], int(a[2]) if len(a) == 3 and a[2].strip() else 0)
        else:
            print(__doc__)
            sys.exit(1)
    except ValueError:
        fail("«сколько» — число")


if __name__ == "__main__":
    main()
