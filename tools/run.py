#!/usr/bin/env python3
"""Цикл Лютика: событие → контекст → решение модели → инструмент → проверка → память → сон.

    python3 tools/run.py --once                  одна сессия и выход
    python3 tools/run.py                         жить: сессия, сон, который назначил сам, снова сессия
    python3 tools/run.py --dry                   без сети, в копии репозитория: модель-заглушка, пост не уходит
    python3 tools/run.py --once --operator "…"   сообщение оператора, приходит один раз

Модель видит только инструменты из TOOLS, шелла у неё нет. Читать можно репозиторий, писать —
GOALS.md и memory/ своей папки, действовать — скриптами agent/scripts/. Это и есть граница
рабочего места; её держит код, поэтому контейнер не нужен. Журнал сессий — logs/, в git не идёт.
"""
import argparse, datetime, json, os, shutil, subprocess, sys, tempfile, threading, time
import http.client, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compose_session import DAYS, compose
from tidy import report as tidy_report

ROOT = Path(__file__).resolve().parents[1]
NAME = "lyutik"
HOME = ROOT / "agent" / NAME
SCRIPTS = ROOT / "agent" / "scripts"
LOGS = ROOT / "logs"
SECRETS = Path.home() / ".config" / "lyutik" / "secrets.env"

MAX_STEPS = 40             # ходов модели за сессию
WRAP_UP = 5                # за столько ходов до конца — напоминание сохранить собранное
DEFAULT_SLEEP = 5          # минут, если персонаж не назначил сон сам: был занят — продолжает почти сразу
RETRY_SLEEP = 5            # минут после сбоя сессии
QUIET_LIMIT = 90           # секунд тишины в потоке ответа: модель думает долго — байты идут; не идут — обрыв, повтор в быструю
HARD_LIMIT = 900           # секунд на один ответ целиком: дольше не ждём даже при живом потоке
TYPING_MAX = 120           # секунд «печатает…» в чате, откуда написали, пока он читает — или до первого ответа
READ_LIMIT, WRITE_LIMIT = 15_000, 20_000   # большие результаты раздувают контекст, и каждый следующий ход ждёт дольше
LOCKED = HOME / "state"    # журнал постов, сон, тикеты: туда пишут только скрипты
OPERATOR = LOCKED / "operator.txt"   # задание оператора: будит и доставляется один раз
AT_WAKE = LOCKED / "operator.at_wake.txt"  # письмо к пробуждению: не будит, отдаётся в начале ближайшей сессии
NEXT_WAKE = LOCKED / "next_wake.txt" # когда проснуться: переживает перезапуск цикла
EVENTS = LOGS / "events.jsonl"       # полный журнал для панели: мысли, речь, действия, результаты
DIALOGS = LOCKED / "dialogs.jsonl"   # личка: что пришло и что он ответил
TG_OFFSET = LOCKED / "tg_offset.txt" # до какого обновления Telegram уже прочитано
THREADS = LOCKED / "threads.json"    # ветка комментариев в группе обсуждения → номер поста в канале
PENDING = LOCKED / "inbox_pending.jsonl"  # подтянутое заранее (старые комментарии): отдаётся при ближайшем опросе
POLLS = LOCKED / "polls.json"        # его опросы: голоса обновляет цикл по событиям Telegram
MEDIA_IN = HOME / "media" / "in"     # фото от людей из лички и комментариев; смотреть — инструментом look


def fn(tool, desc, optional=(), **props):
    return {"type": "function", "function": {"name": tool, "description": desc, "parameters": {
        "type": "object", "required": [k for k in props if k not in optional],
        "properties": {k: {"type": t, "description": d} for k, (t, d) in props.items()}}}}

TOOLS = [
    fn("read_file", "Прочитать файл репозитория. Путь от корня, например agent/lyutik/GOALS.md.",
       path=("string", "путь от корня репозитория")),
    fn("list_dir", "Список файлов в папке репозитория.",
       path=("string", "папка от корня, например agent/lyutik/memory/entities")),
    fn("write_file", "Записать файл целиком. Можно любой файл в agent/lyutik/, кроме папки state/.",
       path=("string", "путь от корня"), content=("string", "полный новый текст файла")),
    fn("post", "Опубликовать пост в своём канале «Полвека поэзии». Текст — Markdown: заголовки, списки, таблицы, цитаты, "
       "**жирный**, и свёрнутые секции <details><summary>заголовок</summary>текст</details> — в ленте они закрыты и "
       "раскрываются по тапу. До 32 768 знаков. Возвращает ссылку на пост или причину отказа.",
       text=("string", "текст поста в Markdown")),
    fn("poll", "Опрос в своём канале: вопрос и 2–12 вариантов, голосуют подписчики. Как проголосовали — polls.",
       question=("string", "вопрос, до 300 знаков"), options=("string", "варианты через «|», каждый до 100 знаков")),
    fn("polls", "Свои опросы и как в них голосуют."),
    fn("edit_post", "Поправить свой пост в канале: новый текст целиком, в Markdown. Номер поста — из ссылки: t.me/pollveka/21 → 21.",
       post=("integer", "номер поста"), text=("string", "новый текст целиком, Markdown")),
    fn("channel_info", "Как мой канал выглядит снаружи: название, описание, аватар, сколько подписчиков."),
    fn("describe_channel", "Сменить описание своего канала — то, что видят под названием. До 255 знаков.",
       text=("string", "новое описание")),
    fn("channel_photo", "Поставить аватар канала из картинки, лежащей в репозитории (jpg или png).",
       path=("string", "путь к картинке от корня репозитория")),
    fn("look", "Посмотреть картинку: описать её или ответить на вопрос о ней. Фото от людей приходят с пометкой «[фото: путь]».",
       optional=("question",), path=("string", "путь к картинке от корня репозитория"),
       question=("string", "что хочешь узнать о картинке; без вопроса — описание")),
    fn("delete_comment", "Удалить чужой комментарий из обсуждения под своими постами. Назад не вернуть.",
       comment=("integer", "номер комментария")),
    fn("ban", "Выгнать автора комментария из обсуждения: он больше не сможет писать под твоими постами. На срок в минутах или, без срока, навсегда. Вернуть — unban.",
       optional=("minutes",), comment=("integer", "номер его комментария"), minutes=("integer", "на сколько минут; без срока — навсегда")),
    fn("unban", "Вернуть выгнанному право писать в обсуждении.",
       comment=("integer", "номер его комментария")),
    fn("delete_post", "Снять свой пост из канала. Назад не вернуть.",
       post=("integer", "номер поста")),
    fn("listen", "Послушать звук: файл из сообщения «[звук: путь]» или ссылку на YouTube и подобные. Возвращает измеренное — "
       "темп, тональность, громкость по времени, части, слова как спеты — и то, как это звучит на слух: инструменты, голос, "
       "настроение, что происходит по времени. Занимает минуту-две.",
       optional=("words",), path=("string", "путь к файлу от корня репозитория или ссылка"),
       words=("boolean", "разбирать ли слова; по умолчанию да")),
    fn("draw", "Нарисовать картинку по описанию. Возвращает путь к файлу — для post_photo или channel_photo. "
       "good — медленно (минута), красиво; fast — секунды, грубее.",
       optional=("mode",), prompt=("string", "что нарисовать, подробно: кто, где, свет, стиль"), mode=("string", "good (по умолчанию) или fast")),
    fn("post_photo", "Опубликовать в канале картинку с подписью (до 1024 знаков). Картинка — из репозитория, например нарисованная draw.",
       path=("string", "путь к картинке от корня репозитория"), caption=("string", "подпись под картинкой")),
    fn("lute", "Сыграть на лютне: мелодия или аккорды уходят голосовым сообщением — в канал (по умолчанию), в личку (chat) "
       "или в ветку комментариев (comment). Без мелодии, но с тональностью — стандартный ход в ней: так можно ответить "
       "треку в его строе и темпе после listen.",
       optional=("melody", "key", "bpm", "repeats", "bass", "caption", "chat", "comment"),
       melody=("string", "ноты или аккорды через пробел: «A3 C4 E4:2 - G3+B3+D4:3», «перебор Am F C G», «бой Gm3:4 Eb3:4 Bb3 F3»; "
               "«:доли» — длительность в четвертях, «-» пауза, «+» аккорд; первое слово — манера: перебор, арпеджио, бой"),
       key=("string", "тональность: «соль минор», «C major», «Gm» — без мелодии лютня сыграет ход в ней"),
       bpm=("integer", "темп, ударов в минуту; по умолчанию 96"),
       repeats=("integer", "сколько раз прокрутить, до 8"), bass=("boolean", "добавить бас под аккорды"),
       caption=("string", "подпись к голосовому: о чём мелодия"),
       chat=("integer", "номер чата, если в личку"), comment=("integer", "номер комментария, если в ветку")),
    fn("sing", "Спеть: твой голос читает текст, лютня играет под ним. Голос — свой, выбранный хозяином; теги настроения в "
       "квадратных скобках по-английски работают: [quietly], [sighs], [chuckles], [pause]. Куда — как у lute.",
       optional=("melody", "key", "bpm", "caption", "chat", "comment"),
       text=("string", "что спеть; первая строка станет подписью, если подпись не дана"),
       melody=("string", "ноты или аккорды, как у lute; без мелодии — перебор в тональности или Am–F–C–G"),
       key=("string", "тональность для аккомпанемента"), bpm=("integer", "темп"),
       caption=("string", "подпись"), chat=("integer", "номер чата, если в личку"), comment=("integer", "номер комментария, если в ветку")),
    fn("remember", "Дописать датированную строку в заметку memory/entities/<name>.md; заметки нет — она появится.",
       name=("string", "имя человека, места, вещи или события"), text=("string", "что запомнить, со ссылками [[имя]]")),
    fn("canon_search", "Найти в книгах о мире, откуда ты пришёл, абзацы, где есть все слова. Слова — начало формы: «лютик» найдёт «Лютика».",
       optional=("page",), query=("string", "слова через пробел"), page=("integer", "страница результатов, с 1")),
    fn("canon_read", "Прочитать абзац из книг вместе с соседними — сцену целиком. Номер берётся из canon_search.",
       id=("integer", "номер абзаца")),
    fn("web_search", "Поиск в сети этого мира: новости, люди, песни, что угодно.",
       query=("string", "запрос")),
    fn("web_read", "Прочитать страницу из сети по адресу.",
       url=("string", "адрес страницы, http(s)://")),
    fn("ticket", "Сообщить разработчику, что чего-то не хватает или что-то сломалось.",
       text=("string", "что нужно или что сломалось, чего ждал и что вышло на деле")),
    fn("reply", "Ответить человеку в личку Telegram. Номер чата приходит вместе с его сообщением.",
       chat=("integer", "номер чата"), text=("string", "текст ответа")),
    fn("reply_comment", "Ответить на комментарий под постом канала — ответ встанет в ту же ветку обсуждения.",
       comment=("integer", "номер комментария"), text=("string", "текст ответа")),
    fn("comments", "Перечитать, что уже говорили в комментариях: под одним постом или последние вообще.",
       optional=("post",), post=("integer", "номер поста в канале")),
    fn("dms", "Перечитать личку: что человек писал и что ты отвечал — по одному чату или последние по всем.",
       optional=("chat",), chat=("integer", "номер чата")),
    fn("tidy", "Проверить память: дубли заметок, ссылки в никуда, даты из будущего, цитаты без следа в журналах, "
       "разросшееся состояние. Ничего не правит — показывает."),
    fn("follow", "Подписки на чужие публичные каналы Telegram: add — подписаться (всё, что там уже есть, считается виденным), "
       "remove — отписаться, list — подписки и сколько в них нового. Закрытые каналы, группы и люди так не читаются.",
       optional=("channel", "why"), action=("string", "add, remove или list"),
       channel=("string", "@имя или ссылка t.me/…"), why=("string", "зачем подписался — одной строкой")),
    fn("news", "Что нового в подписках: новые посты по всем каналам или по одному; показанное становится виденным. "
       "С peek — последние N постов любого публичного канала без подписки и без отметки.",
       optional=("channel", "peek"), channel=("string", "@имя канала"), peek=("integer", "сколько последних постов показать без подписки")),
    fn("sleep", "Назначить следующее пробуждение и закончить сессию.",
       minutes=("integer", "через сколько минут проснуться")),
]


# ── инструменты ───────────────────────────────────────────────────────────────
class Refuse(Exception):
    pass

def resolve(path):
    p = (ROOT / str(path).strip().lstrip("/")).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise Refuse("путь вне репозитория")
    if ".git" in p.relative_to(ROOT).parts:
        raise Refuse("в .git не заглядываем")
    return p

def script(name, *args, dry, timeout=120):
    env = {**os.environ, "AGENT_NAME": NAME, "LYUTIK_DRY": "1" if dry else "0"}
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS / f"{name}.py"), *map(str, args)],
                           capture_output=True, text=True, timeout=timeout, env=env, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return f"ошибка: скрипт не ответил за {timeout // 60} мин"
    out = (r.stdout + r.stderr).strip()
    return out if r.returncode == 0 else f"ошибка (код {r.returncode}): {out}"

def call_tool(name, args, dry):
    try:
        if name == "read_file":
            p = resolve(args["path"])
            if p.is_dir():
                return "это папка — открой её через list_dir"
            if not p.exists():
                return f"нет файла {args['path']}"
            text = p.read_text(encoding="utf-8", errors="replace")
            return text[:READ_LIMIT] + ("\n…(обрезано)" if len(text) > READ_LIMIT else "")
        if name == "list_dir":
            p = resolve(args.get("path") or ".")
            if not p.is_dir():
                return f"нет папки {args.get('path')}"
            items = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir() if not x.name.startswith("."))
            return "\n".join(items) or "(пусто)"
        if name == "write_file":
            p = resolve(args["path"])
            if HOME not in p.parents or p == LOCKED or LOCKED in p.parents:
                return "запись запрещена: писать можно в agent/lyutik/, кроме state/ — туда пишут только инструменты"
            content = args.get("content") or ""
            if len(content) > WRITE_LIMIT:
                return f"слишком длинно: {len(content)} знаков при пределе {WRITE_LIMIT} — сожми"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content.rstrip() + "\n", encoding="utf-8")
            note = ""
            if (HOME / "memory") in p.parents:              # память сверяется с книгой учёта: цитаты без следа — по памяти
                try:
                    sys.path.insert(0, str(SCRIPTS))
                    from _truth import check
                    _, missing = check(content)
                    if missing:
                        note = f"; цитат без следа в журналах: {len(missing)} — " + "; ".join(f"«{m[:50]}»" for m in missing[:3]) + " — это по памяти, не дословно"
                except Exception:
                    pass
            return f"записано: {p.relative_to(ROOT)}, {len(content)} знаков{note}"
        if name == "post":
            return script("post", args["text"], dry=dry)
        if name == "poll":
            options = [o.strip() for o in str(args["options"]).split("|") if o.strip()]
            return script("poll", "new", args["question"], *options, dry=dry)
        if name == "polls":
            return script("poll", "list", dry=dry)
        if name == "edit_post":
            return script("post", "edit", args["post"], args["text"], dry=dry)
        if name == "channel_info":
            return script("channel", "info", dry=dry)
        if name == "describe_channel":
            return script("channel", "describe", args["text"], dry=dry)
        if name == "channel_photo":
            return script("channel", "photo", args["path"], dry=dry)
        if name == "look":
            return script("look", args["path"], args.get("question") or "", dry=dry)
        if name == "delete_comment":
            return script("comment", "delete", args["comment"], dry=dry)
        if name == "ban":
            return script("comment", "ban", args["comment"], args.get("minutes") or 0, dry=dry)
        if name == "unban":
            return script("comment", "unban", args["comment"], dry=dry)
        if name == "delete_post":
            return script("post", "delete", args["post"], dry=dry)
        if name == "listen":
            extra = [] if args.get("words", True) else ["--no-words"]
            return script("listen", args["path"], *extra, dry=dry, timeout=600)
        if name == "draw":
            return script("draw", args["prompt"], args.get("mode") or "good", dry=dry, timeout=300)
        if name == "post_photo":
            return script("post", "photo", args["path"], args["caption"], dry=dry)
        if name in ("lute", "sing"):
            flags = []
            if args.get("key"):
                flags += ["--key", str(args["key"])]
            if args.get("bpm"):
                flags += ["--bpm", str(args["bpm"])]
            if args.get("repeats"):
                flags += ["--repeats", str(args["repeats"])]
            if args.get("bass"):
                flags += ["--bass"]
            text = args.get("text") or "" if name == "sing" else ""
            if name == "lute" and not args.get("melody") and not args.get("key"):
                return "нужна мелодия или тональность"
            tail = [text, args.get("melody") or "", "", args.get("caption") or ""] + flags   # голос — по умолчанию из настроек
            if args.get("comment"):
                return script("voice", "comment", args["comment"], *tail, dry=dry, timeout=240)
            if args.get("chat"):
                return script("voice", "chat", args["chat"], *tail, dry=dry, timeout=240)
            return script("voice", "channel", *tail, dry=dry, timeout=240)
        if name == "remember":
            return script("remember", args["name"], args["text"], dry=dry)
        if name == "canon_search":
            return script("canon", "search", args["query"], args.get("page") or 1, dry=dry)
        if name == "canon_read":
            return script("canon", "read", args["id"], dry=dry)
        if name == "web_search":
            return script("web", "search", args["query"], dry=dry)
        if name == "web_read":
            return script("web", "read", args["url"], dry=dry)
        if name == "ticket":
            return script("ticket", args["text"], dry=dry)
        if name == "reply":
            return script("reply", args["chat"], args["text"], dry=dry)
        if name == "reply_comment":
            return script("comment", "reply", args["comment"], args["text"], dry=dry)
        if name == "comments":
            return script("comment", "list", args.get("post") or "", dry=dry)
        if name == "dms":
            return script("dm", "list", args.get("chat") or "", dry=dry)
        if name == "tidy":
            rows = tidy_report(HOME)
            return "\n".join("- " + r for r in rows) if rows else "память чистая"
        if name == "follow":
            action = str(args.get("action", "list")).strip().lower()
            if action == "list":
                return script("follow", "list", dry=dry)
            if action in ("add", "remove"):
                if not args.get("channel"):
                    return "нужен канал: @имя или ссылка"
                extra = [args["why"]] if action == "add" and args.get("why") else []
                return script("follow", action, args["channel"], *extra, dry=dry)
            return "action — add, remove или list"
        if name == "news":
            if args.get("peek"):
                if not args.get("channel"):
                    return "для peek нужен канал"
                return script("follow", "peek", args["channel"], args["peek"], dry=dry)
            return script("follow", "news", *([args["channel"]] if args.get("channel") else []), dry=dry)
        if name == "sleep":
            return script("sleep", args["minutes"], dry=dry)
        return f"нет такого инструмента: {name}"
    except KeyError as e:
        return f"не хватает аргумента {e}"
    except Refuse as e:
        return f"отказ: {e}"
    except subprocess.TimeoutExpired:
        return "ошибка: скрипт не ответил за две минуты"


# ── модели ────────────────────────────────────────────────────────────────────
class GLM:
    """OpenAI-совместимый чат Z.ai. Провайдер — три строки в secrets.env: адрес, модель, ключ."""
    def __init__(self, s):
        self.url = s["GLM_BASE_URL"].rstrip("/") + "/chat/completions"
        self.key, self.model = s["GLM_API_KEY"], s["GLM_MODEL"]
        self.fallback = s.get("GLM_FALLBACK_MODEL") or "glm-5.3-flash"   # когда основная молчит дольше предела

    def __call__(self, messages):
        for attempt in range(3):
            model = self.model if attempt == 0 else self.fallback
            body = json.dumps({"model": model, "messages": messages, "tools": TOOLS,
                               "tool_choice": "auto", "stream": True}, ensure_ascii=False).encode()
            req = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
            try:
                return self.stream(req)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(20 * (attempt + 1))
                    continue
                raise RuntimeError(f"GLM ответил {e.code}: {detail}")
            except TimeoutError as e:                    # замолчала — следующий заход в быструю модель, без паузы
                if attempt < 2:
                    emit("note", text=f"{model}: {e} — пробую {self.fallback}")
                    continue
                raise RuntimeError(f"нет связи с GLM: {e}")
            except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError, KeyError) as e:
                if attempt < 2:                          # оборванное соединение, пустой ответ — пробуем ещё
                    time.sleep(20)
                    continue
                raise RuntimeError(f"нет связи с GLM: {getattr(e, 'reason', None) or type(e).__name__ + ': ' + str(e)}")

    @staticmethod
    def stream(req):
        """Ответ читается потоком: думает модель хоть пять минут — байты идут, и это не сбой. Тишина дольше
        QUIET_LIMIT — обрыв; HARD_LIMIT — потолок на весь ответ. Куски собираются в обычное сообщение."""
        msg = {"content": "", "reasoning_content": ""}
        calls = {}
        started = time.time()
        with urllib.request.urlopen(req, timeout=QUIET_LIMIT) as r:
            for raw in r:
                if time.time() - started > HARD_LIMIT:
                    raise TimeoutError(f"ответ длиннее {HARD_LIMIT} с")
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    msg["content"] += delta.get("content") or ""
                    msg["reasoning_content"] += delta.get("reasoning_content") or ""
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)),
                                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["function"]["name"] = slot["function"]["name"] or fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
        if calls:
            msg["tool_calls"] = [dict(calls[i], id=calls[i]["id"] or f"call_{i}") for i in sorted(calls)]
        return msg

class FakeModel:
    """Заглушка сухого прогона: тот же цикл теми же инструментами, без сети."""
    STEPS = [
        [("read_file", {"path": "agent/lyutik/GOALS.md"})],
        [("list_dir", {"path": "agent/lyutik/memory/entities"})],
        [("write_file", {"path": "agent/scripts/post.py", "content": "проверка границы"}),
         ("write_file", {"path": "agent/lyutik/state/posts.jsonl", "content": "проверка границы"})],
        [("canon_search", {"query": "лютик эсси"})],
        [("post", {"text": "Сухой прогон. Проверка пути до канала."})],
        [("remember", {"name": "канал", "text": "сухой прогон: пост ушёл в заглушку [[сопряжение]]"})],
        [("write_file", {"path": "agent/lyutik/memory/state.md",
                         "content": "# Состояние — Лютик\n\nСухой прогон: цикл пройден."})],
        [("sleep", {"minutes": 180})],
    ]
    def __init__(self):
        self.i = 0

    def __call__(self, messages):
        if self.i >= len(self.STEPS):
            return {"role": "assistant", "content": "Сессия окончена."}
        calls = self.STEPS[self.i]
        self.i += 1
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"dry{self.i}_{k}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
            for k, (n, a) in enumerate(calls)]}


# ── журнал ────────────────────────────────────────────────────────────────────
def emit(kind, **data):
    """Событие в logs/events.jsonl — его читает панель tools/panel.py. Тексты целиком, без обрезки."""
    LOGS.mkdir(exist_ok=True)
    rec = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind, **data}
    with EVENTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

class Log:
    """Человекочитаемый след сессии: событие → решение → действие → результат. logs/ГГГГ-ММ-ДД.md"""
    def __init__(self, dry):
        LOGS.mkdir(exist_ok=True)
        self.path = LOGS / f"{datetime.date.today():%Y-%m-%d}.md"
        self.dry = dry

    def w(self, line, echo=True):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if echo:
            print(line)

    @staticmethod
    def cut(s, n=400):
        s = " ".join(str(s or "").split())
        return s if len(s) <= n else s[:n] + "…"

    def start(self, wake, operator):
        emit("session", wake=wake, operator=operator or "", dry=self.dry)
        self.w(f"\n## {datetime.datetime.now():%H:%M} · сессия{' · сухой прогон' if self.dry else ''}")
        self.w(f"событие: {wake}" + (f" · от оператора: {self.cut(operator, 200)}" if operator else ""))

    def model(self, step, content, thought, calls):
        if thought:
            emit("thought", step=step, text=thought)
        if content:
            emit("said", step=step, text=content)
        if thought:
            self.w(f"{step}. думает: {self.cut(thought, 300)}")
        if content:
            self.w(f"{step}. говорит: {self.cut(content)}")
        if not calls and not content and not thought:
            self.w(f"{step}. (пустой ход)")

    def tool(self, step, name, args, result):
        emit("tool", step=step, name=name, args=args, result=result[:20000])
        shown = {k: self.cut(v, 160) for k, v in args.items()}
        self.w(f"   → {name}({json.dumps(shown, ensure_ascii=False)}) ⇒ {self.cut(result, 300)}")

    def end(self, minutes, posts):
        wake = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
        emit("session_end", posts=posts, sleep=minutes, wake_at=wake.isoformat(timespec="seconds"))
        self.w(f"итог: постов {len(posts)}" + (f" ({'; '.join(posts)})" if posts else "") +
               f", сон {minutes} мин → {wake:%d.%m %H:%M}")


# ── сессия ────────────────────────────────────────────────────────────────────
class Ears:
    """Личка и комментарии под постами: будят его во сне и доходят посреди сессии."""
    def __init__(self, token, channel=None):
        self.token, self.group, self.bot_id, self.username = token, None, None, None
        try:
            self.bot_id = self.api("getMe")["id"]
            ch = self.api("getChat", chat_id=channel) if channel else {}
            self.group, self.username = ch.get("linked_chat_id"), ch.get("username")
        except Exception:
            pass

    def api(self, method, **params):
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}", method="POST",
                                     data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["result"]

    def action(self, chat, action="typing"):
        """Статус «печатает…» в чате: Telegram держит его 5 секунд; ошибки глотаются — это не повод падать."""
        try:
            self.api("sendChatAction", chat_id=chat, action=action)
        except Exception:
            pass

    def vote(self, p):
        """Новое состояние его опроса — Telegram присылает его при каждом голосе."""
        try:
            d = json.loads(POLLS.read_text(encoding="utf-8")) if POLLS.exists() else {}
        except ValueError:
            d = {}
        rec = d.get(p.get("id"))
        if not rec:
            return
        rec["options"] = [{"text": o["text"], "votes": o.get("voter_count", 0)} for o in p.get("options", [])]
        rec["total"], rec["closed"] = p.get("total_voter_count", 0), p.get("is_closed", False)
        POLLS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        emit("vote", question=rec["question"], total=rec["total"], options=rec["options"])

    def photo(self, m):
        """Фото из сообщения — на диск в agent/lyutik/media/in/. Возвращает путь от корня репозитория или None."""
        sizes = m.get("photo") or []
        if not sizes:
            return None
        try:
            f = self.api("getFile", file_id=sizes[-1]["file_id"])       # последний размер — самый крупный
            ext = Path(f["file_path"]).suffix or ".jpg"
            dest = MEDIA_IN / f"{datetime.datetime.now():%Y-%m-%d_%H%M}_{m.get('message_id')}{ext}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{f['file_path']}", timeout=60) as r:
                dest.write_bytes(r.read())
            return str(dest.relative_to(ROOT))
        except Exception:
            return None

    def sound(self, m):
        """Музыка, голосовое или аудиофайл из сообщения — на диск в media/in/. Возвращает (путь, подпись) или None."""
        a = m.get("audio") or m.get("voice") or (m.get("document") if str((m.get("document") or {}).get("mime_type", "")).startswith("audio/") else None)
        if not a:
            return None
        try:
            f = self.api("getFile", file_id=a["file_id"])
            ext = Path(f["file_path"]).suffix or (".ogg" if m.get("voice") else ".mp3")
            dest = MEDIA_IN / f"{datetime.datetime.now():%Y-%m-%d_%H%M}_{m.get('message_id')}{ext}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{f['file_path']}", timeout=120) as r:
                dest.write_bytes(r.read())
            title = " — ".join(x for x in (a.get("performer"), a.get("title")) if x) or a.get("file_name") or ("голосовое" if m.get("voice") else "звук")
            dur = a.get("duration")
            return str(dest.relative_to(ROOT)), f"{title}" + (f", {dur // 60}:{dur % 60:02d}" if dur else "")
        except Exception:
            return None

    def said(self, m):
        """Текст сообщения с пометками о фото и звуке и путями к ним."""
        text = m.get("text") or m.get("caption") or ""
        path = self.photo(m)
        if path:
            text = (text + "\n" if text else "") + f"[фото: {path} — посмотреть: look]"
        snd = self.sound(m)
        if snd:
            text = (text + "\n" if text else "") + f"[звук: {snd[0]} — {snd[1]} — послушать: listen]"
        return text

    def deliver(self, rec):
        DIALOGS.parent.mkdir(parents=True, exist_ok=True)
        with DIALOGS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        emit(rec.get("kind", "dm"), **{k: v for k, v in rec.items() if k not in ("at", "dir", "kind")})

    def comment(self, m, threads):
        if m.get("is_automatic_forward"):                    # сам пост, продублированный в обсуждение
            post = (m.get("forward_origin") or {}).get("message_id") or m.get("forward_from_message_id")
            threads[str(m["message_id"])] = post
            return None
        if (m.get("from") or {}).get("id") == self.bot_id:  # его собственный ответ
            return None
        rt = m.get("reply_to_message") or {}
        if rt.get("is_automatic_forward"):
            post = (rt.get("forward_origin") or {}).get("message_id") or rt.get("forward_from_message_id")
            threads[str(rt["message_id"])] = post
        else:
            post = threads.get(str(m.get("message_thread_id")))
        fr = m.get("from") or {}
        return {"at": datetime.datetime.now().isoformat(timespec="seconds"), "dir": "in", "kind": "comment",
                "chat": m["chat"]["id"], "message_id": m["message_id"], "post": post, "user_id": fr.get("id"),
                "post_link": f"https://t.me/{self.username}/{post}" if post and self.username else "",
                "name": " ".join(x for x in (fr.get("first_name"), fr.get("last_name")) if x) or fr.get("username") or "кто-то",
                "username": fr.get("username") or "", "text": self.said(m) or "[без текста]",
                "reply_to": rt.get("message_id") if rt and not rt.get("is_automatic_forward") else None}

    def poll(self, timeout=0):
        if PENDING.exists():                                 # подтянутое заранее — отдаём сразу
            recs = [json.loads(x) for x in PENDING.read_text(encoding="utf-8").splitlines() if x.strip()]
            PENDING.unlink()
            for rec in recs:
                if rec.get("requeued"):                      # уже в переписке — только показать заново
                    emit(rec.get("kind", "dm"), **{k: v for k, v in rec.items() if k not in ("at", "dir", "kind", "requeued")})
                else:
                    self.deliver(rec)
            if recs:
                return recs
        if not self.token:
            if timeout:
                time.sleep(min(timeout, 30))
            return []
        offset = int(TG_OFFSET.read_text().strip() or 0) if TG_OFFSET.exists() else 0
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/getUpdates", method="POST",
            data=json.dumps({"offset": offset, "timeout": int(timeout), "allowed_updates": ["message", "poll"]}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout + 20) as r:
                updates = json.loads(r.read().decode()).get("result", [])
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            if timeout:
                time.sleep(5)
            return []
        out = []
        threads = json.loads(THREADS.read_text(encoding="utf-8")) if THREADS.exists() else {}
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
            if u.get("poll"):                                # проголосовали в его опросе
                self.vote(u["poll"])
                continue
            m = u.get("message") or {}
            chat = m.get("chat") or {}
            if chat.get("type") == "private" and (m.get("text") or m.get("photo") or m.get("audio") or m.get("voice") or m.get("document")):
                fr = m.get("from") or {}
                rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "dir": "in", "kind": "dm",
                       "chat": chat["id"], "user_id": fr.get("id"), "username": fr.get("username") or "", "text": self.said(m),
                       "name": " ".join(x for x in (fr.get("first_name"), fr.get("last_name")) if x) or fr.get("username") or "кто-то"}
            elif self.group and chat.get("id") == self.group:
                rec = self.comment(m, threads)
            else:
                rec = None
            if rec:
                self.deliver(rec)
                out.append(rec)
        THREADS.parent.mkdir(parents=True, exist_ok=True)
        THREADS.write_text(json.dumps(threads, ensure_ascii=False), encoding="utf-8")
        TG_OFFSET.parent.mkdir(parents=True, exist_ok=True)
        TG_OFFSET.write_text(str(offset), encoding="utf-8")
        return out

def dm_text(msgs):
    who = lambda m: m["name"] + (f" (@{m['username']})" if m.get("username") else "")
    dms = [m for m in msgs if m.get("kind") != "comment"]
    cms = [m for m in msgs if m.get("kind") == "comment"]
    lines = [f"Сейчас {stamp()}."]
    if any(m.get("requeued") for m in msgs):
        lines.append("Показано повторно: прошлая сессия оборвалась до ответа. Что ты уже успел ответить — видно в comments.")
    if dms:
        lines.append("Тебе пишут в личку Telegram:")
        lines += [f"— {who(m)}, чат {m['chat']}: «{m['text']}»" for m in dms]
        lines.append("Ответить в личку — reply с номером чата.")
    if cms:
        lines.append("Комментарии под постами твоего канала:")
        lines += [f"— под постом {m.get('post_link') or 'неизвестно каким'}, комментарий №{m['message_id']} от {who(m)}"
                  + (f", ответ на №{m['reply_to']}" if m.get("reply_to") else "") + f": «{m['text']}»" for m in cms]
        lines.append("Ответить в комментариях — reply_comment с номером комментария; что уже говорили — comments.")
    return "\n".join(lines)


class Typist(threading.Thread):
    """«Лютик печатает…» там, откуда написали, пока он читает: до TYPING_MAX секунд или до первого ответа."""
    def __init__(self, ears, chats):
        super().__init__(daemon=True)
        self.ears, self.chats, self.done = ears, set(chats), threading.Event()

    def run(self):
        end = time.time() + TYPING_MAX
        while time.time() < end and not self.done.is_set():
            for c in self.chats:
                self.ears.action(c)
            self.done.wait(4)

    def stop(self):
        self.done.set()


def stamp():
    n = datetime.datetime.now()
    return f"{DAYS[n.weekday()]}, {n:%d.%m.%Y %H:%M}"


def morning(wake):
    """После сна по своему плану — уборка памяти: что разъехалось, пока он жил. Показывает, не правит."""
    if not str(wake).startswith("проснулся по своему плану"):
        return ""
    try:
        rows = tidy_report(HOME)
    except Exception:
        return ""
    if not rows:
        return "\nПока спал, память проверил — чистая."
    return "\nПока спал, память проверил — вот что разъехалось, правь по своему разумению:\n" + "\n".join("- " + r for r in rows)


def requeue(msgs):
    """Письма, которые сессия показала, но не успела отработать, — обратно в ящик с пометкой «повтор»."""
    if msgs:
        PENDING.write_text("".join(json.dumps({**m, "requeued": True}, ensure_ascii=False) + "\n" for m in msgs),
                           encoding="utf-8")


def session(model, dry, operator, wake, ears=None, pending=(), delivered=None):
    """delivered — всё от людей, что сессия показала модели: при сбое вернётся в ящик с пометкой «повтор»."""
    delivered = [] if delivered is None else delivered
    delivered[:] = list(pending)
    log = Log(dry)
    started = time.time()
    for box in (OPERATOR, AT_WAKE):    # ящик: один раз, потом убирается с отметкой времени
        if box.exists():
            mail = box.read_text(encoding="utf-8").strip()
            box.rename(LOCKED / f"{box.stem}.{datetime.datetime.now():%Y%m%d-%H%M}.delivered.txt")
            operator = "\n\n".join(x for x in (operator, mail) if x)
    messages = [{"role": "system", "content": compose(NAME, operator, wake=wake, dry=dry)},
                {"role": "user", "content": dm_text(pending) if pending else f"Проснулся. Сейчас {stamp()}. Сессия началась." + morning(wake)}]
    log.start(wake, operator)
    posts, slept, nudged = [], False, False
    typist = None

    def listen(msgs):                                 # «печатает…» тем, кто написал, пока он читает
        nonlocal typist
        chats = {m["chat"] for m in msgs if m.get("chat")}
        if ears and chats:
            if typist:
                typist.stop()
            typist = Typist(ears, chats)
            typist.start()

    listen(pending)
    for step in range(1, MAX_STEPS + 1):
        if step == MAX_STEPS - WRAP_UP + 1:
            messages.append({"role": "user", "content": f"Осталось {WRAP_UP} ходов в этой сессии: сохрани собранное в память. Сон — по желанию: не назначишь — продолжишь через несколько минут."})
            log.w(f"{step}. (напоминание: осталось {WRAP_UP} ходов)")
            emit("note", step=step, text=f"напоминание: осталось {WRAP_UP} ходов")
        emit("calling", step=step)
        msg = model(messages)
        calls = msg.get("tool_calls") or []
        log.model(step, msg.get("content"), msg.get("reasoning_content"), calls)
        turn = {"role": "assistant", "content": msg.get("content") or ""}
        if calls:
            turn["tool_calls"] = calls
        messages.append(turn)
        if not calls:
            if nudged:                 # второй ход словами подряд — сессия закончена
                break
            nudged = True              # первый — напоминаю, что сессия идёт, пока не назначен сон
            messages.append({"role": "user", "content": "Сессия идёт: действуй инструментами."})
            emit("note", step=step, text="ответил словами без действия — напоминаю, что сессия идёт")
            continue
        for c in calls:
            name = c["function"]["name"]
            try:
                raw = c["function"].get("arguments") or "{}"
                args = raw if isinstance(raw, dict) else json.loads(raw)
                result = call_tool(name, args, dry)
            except ValueError:
                args, result = {}, "аргументы не разобрались как JSON — повтори вызов"
            log.tool(step, name, args, result)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
            if name in ("reply", "reply_comment") and typist:
                typist.stop()
            slept |= name == "sleep" and not result.startswith("ошибка")
            if name == "post" and not result.startswith(("ошибка", "не опубликовано")):
                posts.append(result)
        fresh = ears.poll(0) if ears else []          # написали, пока он работал — услышит сразу
        if fresh:
            messages.append({"role": "user", "content": dm_text(fresh)})
            delivered.extend(fresh)
            listen(fresh)
            slept = False
        if slept:
            break
    else:
        log.w(f"сессия упёрлась в предел {MAX_STEPS} ходов")
        emit("note", step=MAX_STEPS, text=f"сессия упёрлась в предел {MAX_STEPS} ходов")
    if typist:
        typist.stop()
    sleep_file = HOME / "state" / "sleep.txt"
    minutes = DEFAULT_SLEEP
    if sleep_file.exists() and sleep_file.stat().st_mtime >= started:
        minutes = int(sleep_file.read_text().strip() or DEFAULT_SLEEP)
    log.end(minutes, posts)
    return minutes


def nap(until, reason, ears=None):
    """Спать до until. Будят раньше задание в ящике и сообщение в личке. Возвращает (почему проснулся, сообщения)."""
    NEXT_WAKE.parent.mkdir(parents=True, exist_ok=True)
    NEXT_WAKE.write_text(until.isoformat(timespec="seconds") + "\n", encoding="utf-8")
    emit("sleeping", until=until.isoformat(timespec="seconds"))
    print(f"сплю до {until:%d.%m %H:%M}. Ctrl+C — остановить.")
    while datetime.datetime.now() < until:
        if OPERATOR.exists():
            return "разбудило задание оператора", []
        left = max(1, min(25, (until - datetime.datetime.now()).total_seconds()))
        msgs = ears.poll(left) if ears else []
        if not ears:
            time.sleep(left)
        if msgs:
            return "разбудили люди: " + ", ".join(sorted({m["name"] for m in msgs})), msgs
    return reason, []


def secrets():
    data = {}
    if SECRETS.exists():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"')
    return data


def main():
    ap = argparse.ArgumentParser(description="цикл Лютика")
    ap.add_argument("--once", action="store_true", help="одна сессия и выход")
    ap.add_argument("--dry", action="store_true", help="без сети, в копии репозитория")
    ap.add_argument("--operator", default=None, help="сообщение оператора, приходит один раз")
    a = ap.parse_args()

    if a.dry and not os.environ.get("LYUTIK_DRY_COPY"):
        copy = Path(tempfile.mkdtemp(prefix="lyutik-dry-")) / "lyutik"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "logs", "__pycache__"))
        print(f"сухой прогон в копии: {copy}")
        sys.exit(subprocess.call([sys.executable, str(copy / "tools" / "run.py"), *sys.argv[1:]],
                                 env={**os.environ, "LYUTIK_DRY_COPY": "1"}))

    ears = None
    if a.dry:
        model = FakeModel()
    else:
        s = secrets()
        missing = [k for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "GLM_API_KEY", "GLM_BASE_URL",
                               "GLM_MODEL") if not s.get(k)]
        if missing:
            sys.exit(f"нет ключей: {', '.join(missing)}. Запусти: python3 tools/keys.py")
        model = GLM(s)
        ears = Ears(s["TELEGRAM_BOT_TOKEN"], s.get("TELEGRAM_CHANNEL_ID"))

    operator, wake, pending = a.operator, "первое пробуждение после запуска", []
    emit("start", pid=os.getpid(), dry=a.dry)
    try:
        if not (a.once or a.dry or operator) and NEXT_WAKE.exists():   # перезапуск посреди сна — досыпает
            try:
                until = datetime.datetime.fromisoformat(NEXT_WAKE.read_text().strip())
            except ValueError:
                until = None
            if until and until > datetime.datetime.now():
                wake, pending = nap(until, "проснулся по своему плану", ears)
        while True:
            delivered = []
            try:
                minutes = session(model, a.dry, operator, wake, ears, pending, delivered)
            except KeyboardInterrupt:                  # остановили руками — письма дождутся следующего запуска
                requeue(delivered)
                raise
            except Exception as e:                     # одна сессия не должна ронять весь цикл
                Log(a.dry).w(f"сбой сессии: {type(e).__name__}: {e}. Повтор через {RETRY_SLEEP} мин")
                emit("error", text=str(e))
                minutes = RETRY_SLEEP
                requeue(delivered)                     # кто написал, увидит ответ в следующей попытке
            operator, pending = None, []
            if a.once or a.dry:
                return
            until = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
            wake, pending = nap(until, f"проснулся по своему плану: спал {minutes} мин", ears)
    except KeyboardInterrupt:
        emit("stopped")
        print("\nостановлен")


if __name__ == "__main__":
    main()
