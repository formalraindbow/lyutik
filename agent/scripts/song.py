#!/usr/bin/env python3
"""Песни с настоящим вокалом: сочинить, нарезать сниппет, выпустить.

    song.py make "<название>" "<звук>" "<текст песни>" [--cover "<что на обложке>"]
    song.py snippet <путь к песне> [секунд] [с какой секунды]
    song.py channel <путь> ["<подпись>"] [--cover <путь к картинке>]
    song.py chat <номер чата> <путь> ["<подпись>"] [--cover <путь к картинке>]
    song.py comment <номер комментария> <путь> ["<подпись>"] [--cover <путь к картинке>]
    song.py release <путь к песне> "<подпись>" [--cover <путь или описание>] [--start с] [--seconds с]
    song.py lyrics <номер сообщения в обсуждении> <путь к песне>

make — песня через Mureka (шлюз NanoGPT, ключ LLM_API_KEY): текст в разметке [Verse] / [Chorus], звук — пара фраз
по-английски (жанр, инструменты, темп). Голос — свой клон (MUREKA_VOCAL_ID в secrets.env, модель mureka/api/generate/song);
без него — голос модели по умолчанию (Mureka V9.5). Генерация идёт минуту-две. Песня длиннее SONG_MAX_SECONDS (по умолчанию 150)
обрезается с затуханием — это крайняя мера: обрезка съедает концовку. За сутки — не больше SONG_DAILY_LIMIT песен (по умолчанию 3): каждая стоит денег.
С --cover обложка рисуется через draw.generate (anymodel, модель COVER_MODEL), пока поётся песня; не вышла — песня готова.
Песни лежат в agent/lyutik/media/songs/, обложки — в media/covers/, список — state/songs.jsonl.
snippet — кусок песни, по умолчанию 25 секунд с трети записи, с мягким входом и затуханием; ложится рядом с песней.
channel / chat / comment — отправить песню или сниппет как музыкальный трек: исполнитель «Лютик», название из списка песен.
Обложка — из --cover (путь к картинке; любой другой текст — описание, обложка рисуется), иначе последняя, назначенная
песне (кусок берёт обложку своей песни). Она уходит дважды: внутри mp3 (ID3, 800 px — видна у скачавших) и превью
для Telegram (jpeg до 320 px и 200 КБ, так требует sendAudio).
release — релиз в канал: видео-сниппет (обложка в кадре и кусок песни, 25 с с трети записи) с подписью,
следом — вся песня треком с обложкой. Текст песни уходит комментарием под релиз сам: release оставляет отметку
в state/lyrics_pending.json, цикл (run.py) видит пост в обсуждении и зовёт lyrics.
lyrics — текст песни из songs.jsonl комментарием под постом: ответ на его копию в группе обсуждения.
"""
import html, json, re, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path
from _common import DRY, HOME, ROOT, STATE, append_jsonl, md_caption, now, plain_caption, read_jsonl, secrets, tg_link
import draw

SONGS_DIR = HOME / "media" / "songs"
COVERS_DIR = HOME / "media" / "covers"
COVER_FRAME = "Обложка музыкального трека, квадрат. {desc}\nНикаких надписей, букв и логотипов."
COVER_MAX = 1500
COVER_MODEL = "anymedia/seedream-5-pro"   # замер 08.11: ближе всех к фото; gpt-image глянцевый, nano-banana-pro путал лютню с гитарой
SONGS, POSTS, DIALOGS = STATE / "songs.jsonl", STATE / "posts.jsonl", STATE / "dialogs.jsonl"
LYRICS_PENDING = STATE / "lyrics_pending.json"     # пост канала → песня: текст повиснет комментарием, когда пост дойдёт до обсуждения
MODEL, COST = "mureka-ai/mureka-v9.5/generate-song", 0.225          # без своего голоса
VOICE_MODEL, VOICE_COST = "mureka/api/generate/song", 0.15           # со своим голосом: vocal_id видит только эта обёртка
API = "https://api.nano-gpt.com/api"
UA = "Mozilla/5.0 (Macintosh) lyutik/1.0"
MAX_LINES = 16
# Лютик — независимый исполнитель (ядро, п. 12): один голос, без подпевок, хора и «завываний»; дописывается к звуку
SOLO = ("solo male vocal, one voice only, close-mic dry vocal, plain direct delivery, "
        "no backing vocals, no choir, no harmonies, no vocal runs, sparse arrangement")


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def setting(name, default):
    try:
        return int(secrets().get(name) or default)
    except ValueError:
        return default

def seconds_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        fail(f"не читается звук {path.name}: {r.stderr.strip()[:200]}")

def ffmpeg(*args, soft=False):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], capture_output=True, text=True)
    if r.returncode:
        if soft:
            raise RuntimeError(f"ffmpeg: {r.stderr.strip()[:200]}")
        fail(f"ffmpeg: {r.stderr.strip()[:300]}")

def slug(title):
    s = re.sub(r"[^\w]+", "_", title.lower(), flags=re.U).strip("_")
    return s[:40] or "песня"

def local(path_arg):
    p = Path(path_arg)
    p = p if p.is_absolute() else ROOT / p
    if not p.exists():
        fail(f"нет файла {path_arg}")
    return p

def rel(p):
    return str(p.relative_to(ROOT))

def songbook():
    return [s for s in read_jsonl(SONGS) if not s.get("dry")]

def entry_for(path):
    name = path.name
    for s in reversed(read_jsonl(SONGS)):
        if Path(s.get("path", "")).name == name or Path(s.get("snippet", "")).name == name:
            return s
    return {}

def nano(method, url, body=None):
    key = secrets().get("LLM_API_KEY")
    if not key:
        fail("нет ключа NanoGPT (LLM_API_KEY в secrets.env)")
    headers = {"User-Agent": UA, "x-api-key": key, "Authorization": f"Bearer {key}"}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body, ensure_ascii=False).encode(), "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        fail(f"NanoGPT ответил {e.code}: {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, TimeoutError) as e:
        fail(f"нет связи с NanoGPT: {getattr(e, 'reason', e)}")

def make(title, sound, lyrics, cover=""):
    title, sound, lyrics, cover = title.strip(), sound.strip(), lyrics.strip(), (cover or "").strip()
    lyrics = "\n".join(c for l in lyrics.splitlines()            # в скобках модель поёт вторым голосом — он поёт один
                       for c in [re.sub(r"[ \t]*\([^)\n]*\)", "", l).rstrip()] if c.strip() or not l.strip())
    if not title or not sound or not lyrics:
        fail("нужны название, звук и текст")
    sung = [l for l in lyrics.splitlines() if l.strip() and not re.match(r"^\s*\[.*\]\s*$", l)]
    if len(sung) > MAX_LINES:
        fail(f"в тексте {len(sung)} строк — на полторы минуты хватает куплета и припева, до {MAX_LINES} строк")
    if len(cover) > COVER_MAX:
        fail(f"описание обложки длиннее {COVER_MAX} знаков — одна картинка, коротко")
    t, started = now(), time.monotonic()
    today = [s for s in songbook() if s.get("path") and s.get("at", "")[:10] == t.date().isoformat()]   # только сочинённые
    limit = setting("SONG_DAILY_LIMIT", 3)
    if len(today) >= limit:
        fail(f"сегодня уже {len(today)} песен из {limit} — следующая завтра")
    SONGS_DIR.mkdir(parents=True, exist_ok=True)
    out = SONGS_DIR / f"{t:%Y-%m-%d_%H%M}_{slug(title)}.mp3"
    if DRY:
        ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "60", "-q:a", "9", out)
        append_jsonl(SONGS, {"at": t.isoformat(timespec="seconds"), "title": title, "sound": sound, "lyrics": lyrics,
                             "path": rel(out), "seconds": 60, "dry": True})
        print(f"[сухой прогон] песня «{title}» не заказана, вместо неё тишина 60 с: {rel(out)}"
              + (f"; обложку не рисовал: {cover[:60]}" if cover else "; без обложки"))
        return
    art = {"path": None, "error": "не заказана" if not cover else None}
    def paint():
        try:
            art["path"] = draw.generate(COVER_FRAME.format(desc=cover), "good", COVERS_DIR / f"{out.stem}.png",
                                        model=secrets().get("COVER_MODEL") or COVER_MODEL)
        except Exception as e:        # обложка не должна ронять песню, за которую уже заплачено
            art["error"] = str(e)[:200]
    painter = threading.Thread(target=paint, daemon=True)
    if cover:
        painter.start()
    vocal = secrets().get("MUREKA_VOCAL_ID")
    model, cost = (VOICE_MODEL, VOICE_COST) if vocal else (MODEL, COST)
    style = f"{sound.rstrip(' .')}. {SOLO}"
    body = {"model": model, "input": style, "prompt": style, "lyrics": lyrics, "response_format": "mp3"}
    if vocal:
        body["vocal_id"] = vocal
    job = nano("POST", f"{API}/v1/audio/speech", body)
    run = job.get("runId")
    if not run:
        fail(f"NanoGPT не дал номер заказа: {json.dumps(job, ensure_ascii=False)[:200]}")
    q = urllib.parse.urlencode({"runId": run, "model": model, "cost": job.get("cost", cost),
                                "paymentSource": job.get("paymentSource", "USD"), "isApiRequest": "true"})
    for _ in range(40):
        time.sleep(10)
        st = nano("GET", f"{API}/tts/status?{q}")
        if st.get("status") == "completed" and st.get("audioUrl"):
            with urllib.request.urlopen(urllib.request.Request(st["audioUrl"], headers={"User-Agent": UA}), timeout=120) as r:
                raw = r.read()
            break
        if st.get("status") == "error":
            fail(f"Mureka не спела: {st.get('error') or st.get('code') or st}")
    else:
        fail(f"песня не готова за 7 минут; заказ {run}")
    full = out.with_suffix(".full.mp3")
    full.write_bytes(raw)
    seconds, cut = seconds_of(full), setting("SONG_MAX_SECONDS", 150)   # 16 строк Mureka поёт за 85–105 с, с концовкой
    if seconds > cut:
        ffmpeg("-i", full, "-t", cut, "-af", f"afade=t=out:st={cut - 4}:d=4", "-b:a", "192k", out)
        seconds = cut
    else:
        full.rename(out)
    if cover:                         # весь make укладывается в 8 минут, которые даёт ему цикл
        painter.join(timeout=max(5, min(240, 450 - (time.monotonic() - started))))
        if not art["path"] and not art["error"]:
            art["error"] = "художник не успел"
    entry = {"at": t.isoformat(timespec="seconds"), "title": title, "sound": sound, "lyrics": lyrics,
             "path": rel(out), "seconds": round(seconds), "cost": job.get("cost", cost), "run": run,
             "voice": vocal or "по умолчанию", "style": style}
    if art["path"]:
        entry.update(cover=rel(art["path"]), cover_prompt=cover)
    append_jsonl(SONGS, entry)
    drawn = (f" Обложка: {rel(art['path'])} — выйдет вместе с треком." if art["path"] else
             f" Обложки нет ({art['error']}): можно нарисовать draw и отдать post_song в cover.")
    print(f"песня «{title}» готова: {rel(out)}, {seconds:.0f} с, ${job.get('cost', cost)}"
          f"{', своим голосом' if vocal else ''}.{drawn} "
          f"Послушать — listen, выпустить — post_song, кусок на затравку — song_snippet.")

def snippet(path_arg, length=25.0, start=None):
    src = local(path_arg)
    total = seconds_of(src)
    length = max(8.0, min(float(length), 45.0, total))
    start = float(start) if start not in (None, "") else total / 3
    start = max(0.0, min(start, total - length))
    out = src.with_name(f"{src.stem}.snippet_{int(start)}s.mp3")
    ffmpeg("-ss", f"{start:.1f}", "-t", f"{length:.1f}", "-i", src,
           "-af", f"afade=t=in:st=0:d=0.6,afade=t=out:st={length - 2:.1f}:d=2", "-b:a", "192k", out)
    e = entry_for(src)
    if e:
        append_jsonl(SONGS, {**{k: e[k] for k in ("title", "sound") if k in e}, "at": now().isoformat(timespec="seconds"),
                             "snippet": rel(out), "of": e.get("path"), "seconds": round(length), "dry": DRY or None})
    print(f"сниппет готов: {rel(out)}, {length:.0f} с с {start:.0f}-й секунды")

def tg(token, method, data, files, soft=False):
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in data.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (name, blob, mime) in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                 f"Content-Type: {mime}\r\n\r\n").encode() + blob + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", method="POST", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode())["result"]
    except urllib.error.HTTPError as e:
        desc = json.loads(e.read().decode() or '{}').get('description', '')
        if soft:
            raise RuntimeError(desc)
        fail(f"Telegram ответил {e.code}: {desc}")
    except urllib.error.URLError as e:
        fail(f"нет связи с Telegram: {e.reason}")

def tg_captioned(token, method, build, files):
    """Подпись сначала с разметкой (Markdown → HTML); не разобрал её Telegram — тот же текст без разметки."""
    try:
        return tg(token, method, build(True), files, soft=True)
    except RuntimeError as e:
        if not re.search(r"(?i)pars|entit|tag", str(e)):
            fail(f"Telegram ответил: {e}")
        return tg(token, method, build(False), files)

def with_caption(data, caption, rich):
    cap = (caption or "").strip()[:1000]
    if not cap:
        return dict(data)
    return {**data, "caption": md_caption(cap), "parse_mode": "HTML"} if rich else {**data, "caption": plain_caption(cap)}

def cover_of(src):
    """Последняя обложка, назначенная этой песне; кусок берёт обложку своей песни."""
    keys = {rel(src), entry_for(src).get("of")} - {None}
    for s in reversed(read_jsonl(SONGS)):
        if s.get("cover") and (s.get("path") in keys or s.get("cover_for") in keys) and (ROOT / s["cover"]).exists():
            return ROOT / s["cover"]
    return None

def dress(src, cover, title, tmp):
    """Трек с обложкой: она внутри mp3 (ID3, 800 px) и отдельным превью для Telegram (jpeg до 320 px и 200 КБ)."""
    square = "scale={0}:{0}:force_original_aspect_ratio=increase,crop={0}:{0}"
    art, thumb, tagged = tmp / "art.jpg", tmp / "thumb.jpg", tmp / src.name
    ffmpeg("-i", cover, "-vf", square.format(800), "-q:v", 3, art, soft=True)
    for q in (3, 6, 10):
        ffmpeg("-i", cover, "-vf", square.format(320), "-q:v", q, thumb, soft=True)
        if thumb.stat().st_size < 200_000:
            break
    ffmpeg("-i", src, "-i", art, "-map", "0:a", "-map", "1:0", "-c", "copy", "-id3v2_version", 3,
           "-metadata", f"title={title}", "-metadata", "artist=Лютик",
           "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)", tagged, soft=True)
    return tagged.read_bytes(), thumb.read_bytes()

def resolve_cover(arg, src, title):
    """Обложка для выпуска: путь к картинке, описание (тогда рисуется) или пусто — обложка песни.
    Назначенная или нарисованная остаётся за песней: её возьмут куски и повторные выпуски."""
    arg = (arg or "").strip()
    if not arg:
        return cover_of(src)
    song_key = entry_for(src).get("of") or rel(src)
    if "\n" not in arg and re.search(r"\.(png|jpe?g|webp)$", arg, re.I):   # путь к картинке; всё прочее — описание
        p = Path(arg)
        p = p if p.is_absolute() else ROOT / p
        if not p.exists():
            fail(f"нет файла {arg}")
        prompt = None
    else:
        if len(arg) > COVER_MAX:
            fail(f"описание обложки длиннее {COVER_MAX} знаков — одна картинка, коротко")
        if DRY:
            print(f"[сухой прогон] обложку не рисовал: {arg[:60]}")
            return None
        try:
            p = draw.generate(COVER_FRAME.format(desc=arg), "good", COVERS_DIR / f"{Path(song_key).stem}_{now():%H%M%S}.png",
                              model=secrets().get("COVER_MODEL") or COVER_MODEL)
        except draw.DrawError as err:
            fail(f"обложка не нарисовалась: {err}")
        prompt = arg
    if not DRY:
        append_jsonl(SONGS, {"at": now().isoformat(timespec="seconds"), "title": title, "cover_for": song_key,
                             "cover": rel(p), **({"cover_prompt": prompt} if prompt else {})})
    return p

def lyrics_later(post_id, src):
    try:
        pend = json.loads(LYRICS_PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pend = {}
    pend[str(post_id)] = rel(src)
    LYRICS_PENDING.write_text(json.dumps(pend, ensure_ascii=False), encoding="utf-8")

def lyrics_html(title, lyrics):
    """Текст для людей: без разметки [Verse]/[Chorus], куплеты через пустую строку, название жирным."""
    blocks, cur = [], []
    for line in lyrics.splitlines():
        if not line.strip() or re.match(r"^\s*\[.*\]\s*$", line):
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(html.escape(line.strip()))
    if cur:
        blocks.append(cur)
    return f"<b>«{html.escape(title)}»</b>\n\n" + "\n\n".join("\n".join(b) for b in blocks)

def discussion(token, channel):
    group = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getChat", data=json.dumps({"chat_id": channel}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30).read().decode())["result"].get("linked_chat_id")
    if not group:
        fail("у канала нет группы обсуждения")
    return group

def lyrics_comment(reply_to, path_arg):
    """Текст песни — комментарием под постом: ответ на его копию в группе обсуждения."""
    src = local(path_arg)
    e = entry_for(src)
    if e.get("of"):
        e = next((x for x in reversed(read_jsonl(SONGS)) if x.get("path") == e["of"] and x.get("lyrics")), e)
    if not e.get("lyrics"):
        fail(f"у {src.name} нет текста в songs.jsonl")
    title = e.get("title") or src.stem
    if DRY:
        print(f"[сухой прогон] текст «{title}» под сообщением {reply_to} не отправлен")
        return
    s = secrets()
    token, channel = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    group = discussion(token, channel)
    msg = tg(token, "sendMessage", {"chat_id": group, "text": lyrics_html(title, e["lyrics"]), "parse_mode": "HTML",
                                    "reply_parameters": json.dumps({"message_id": int(reply_to), "allow_sending_without_reply": True})}, {})
    post = next((d.get("post") for d in read_jsonl(DIALOGS) if d.get("chat") == group and d.get("message_id") == int(reply_to)), None)
    append_jsonl(DIALOGS, {"at": now().isoformat(timespec="seconds"), "dir": "out", "kind": "comment", "post": post,
                           "chat": group, "reply_to": int(reply_to), "message_id": msg["message_id"],
                           "text": f"текст песни «{title}» в комментариях"})
    print(f"текст «{title}» — в комментариях, сообщение {msg['message_id']}")

def send(where, target, path_arg, caption="", cover_arg="", cover=None, lyrics=True):
    src = local(path_arg)
    e = entry_for(src)
    title = e.get("title") or src.stem
    is_snippet = ".snippet_" in src.name
    seconds = seconds_of(src)
    t = now()
    cover = cover or resolve_cover(cover_arg, src, title)
    if DRY:
        print(f"[сухой прогон] {'сниппет' if is_snippet else 'песня'} «{title}», {seconds:.0f} с, "
              f"{'обложка ' + rel(cover) if cover else 'без обложки'} — не отправлено")
        return
    s = secrets()
    token, channel = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not channel:
        fail("нет ключей бота или канала — запусти tools/keys.py")
    data = {"title": title + (" (кусок)" if is_snippet else ""), "performer": "Лютик", "duration": int(seconds)}
    files = {"audio": (src.name, src.read_bytes(), "audio/mpeg")}
    note = ""
    if cover:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                audio, thumb = dress(src, cover, title, Path(tmp))
            files = {"audio": (src.name, audio, "audio/mpeg"), "cover": ("cover.jpg", thumb, "image/jpeg")}
            data["thumbnail"] = "attach://cover"
            note = ", с обложкой"
        except (RuntimeError, OSError) as err:
            cover, note = None, f", без обложки — не прицепилась: {str(err)[:120]}"
    label = f"🎵 «{title}»" + (" — кусок" if is_snippet else "") + (f"\n{caption.strip()}" if caption.strip() else "")
    if where == "channel":
        msg = tg_captioned(token, "sendAudio", lambda rich: with_caption({**data, "chat_id": channel}, caption, rich), files)
        link = tg_link(msg)
        append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": label, "link": link,
                             "message_id": msg["message_id"], "format": "song", "seconds": int(seconds), "file": rel(src),
                             "cover": rel(cover) if cover else None})
        append_jsonl(SONGS, {"at": t.isoformat(timespec="seconds"), "title": title, "posted": link, "file": rel(src)})
        if lyrics and not is_snippet:
            lyrics_later(msg["message_id"], src)
        print(f"выпущено в канал {t:%d.%m %H:%M}: «{title}», {seconds:.0f} с{note} — {link}")
        return link
    elif where == "chat":
        tg_captioned(token, "sendAudio", lambda rich: with_caption({**data, "chat_id": target}, caption, rich), files)
        append_jsonl(DIALOGS, {"at": t.isoformat(timespec="seconds"), "dir": "out", "kind": "dm", "chat": target,
                               "text": label, "voice": True})
        print(f"отправлено в личку {target}: «{title}», {seconds:.0f} с{note}")
    else:
        group = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getChat", data=json.dumps({"chat_id": channel}).encode(),
            headers={"Content-Type": "application/json"}), timeout=30).read().decode())["result"].get("linked_chat_id")
        if not group:
            fail("у канала нет группы обсуждения")
        msg = tg_captioned(token, "sendAudio", lambda rich: with_caption({**data, "chat_id": group, "reply_parameters":
                           json.dumps({"message_id": target, "allow_sending_without_reply": True})}, caption, rich), files)
        post = next((d.get("post") for d in read_jsonl(DIALOGS)
                     if d.get("kind") == "comment" and d.get("message_id") == target), None)
        append_jsonl(DIALOGS, {"at": t.isoformat(timespec="seconds"), "dir": "out", "kind": "comment", "post": post,
                               "chat": group, "reply_to": target, "message_id": msg["message_id"], "text": label, "voice": True})
        print(f"отправлено в ответ на №{target}: «{title}», {seconds:.0f} с{note}")


def release(path_arg, caption, cover_arg="", start=None, length=None):
    """Релиз: видео-сниппет (обложка в кадре, кусок песни) с подписью, следом вся песня треком с обложкой."""
    src = local(path_arg)
    if ".snippet_" in src.name:
        fail("релиз собирается из целой песни — сниппет нарежется сам")
    caption = (caption or "").strip()
    if not caption:
        fail("нужна подпись под сниппетом")
    if len(caption) > 1024:
        fail(f"подпись {len(caption)} знаков — Telegram берёт до 1024")
    title = entry_for(src).get("title") or src.stem
    cover = resolve_cover(cover_arg, src, title)
    if not cover and not DRY:
        fail("у песни нет обложки: опиши её в cover — нарисую, или дай путь к картинке")
    total = seconds_of(src)
    length = max(8.0, min(float(length or 25), 45.0, total))
    start = float(start) if start not in (None, "") else total / 3
    start = max(0.0, min(start, total - length))
    if DRY:
        print(f"[сухой прогон] релиз «{title}»: {'обложка ' + rel(cover) if cover else 'обложка не нарисована'}, "
              f"сниппет {length:.0f} с с {start:.0f}-й секунды, подпись {len(caption)} знаков — не отправлено")
        return
    s = secrets()
    token, channel = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not channel:
        fail("нет ключей бота или канала — запусти tools/keys.py")
    square = "scale={0}:{0}:force_original_aspect_ratio=increase,crop={0}:{0}"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        thumb, clip = tmp / "thumb.jpg", tmp / "snippet.mp4"
        ffmpeg("-i", cover, "-vf", square.format(320), "-q:v", 4, thumb)
        ffmpeg("-loop", 1, "-framerate", 25, "-i", cover, "-ss", f"{start:.1f}", "-t", f"{length:.1f}", "-i", src,
               "-map", "0:v", "-map", "1:a", "-vf", square.format(1080) + ",format=yuv420p",
               "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast", "-crf", 24,
               "-af", f"afade=t=in:st=0:d=0.6,afade=t=out:st={length - 2:.1f}:d=2", "-c:a", "aac", "-b:a", "192k",
               "-t", f"{length:.1f}", "-shortest", "-movflags", "+faststart", clip)
        video = {"chat_id": channel, "video": "attach://clip", "thumbnail": "attach://thumb", "width": 1080,
                 "height": 1080, "duration": int(round(length)), "supports_streaming": "true"}
        msg = tg_captioned(token, "sendVideo", lambda rich: with_caption(video, caption, rich),
                           {"clip": ("snippet.mp4", clip.read_bytes(), "video/mp4"),
                            "thumb": ("thumb.jpg", thumb.read_bytes(), "image/jpeg")})
    t = now()
    link = tg_link(msg)
    append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": f"🎵 «{title}» — релиз\n{caption}", "link": link,
                         "message_id": msg["message_id"], "format": "release", "file": rel(src), "cover": rel(cover),
                         "snippet": [round(start), round(length)]})
    lyrics_later(msg["message_id"], src)
    print(f"релиз «{title}» в канале {t:%d.%m %H:%M}: видео-сниппет {length:.0f} с с {start:.0f}-й секунды — {link}; "
          f"текст песни повиснет в комментариях сам")
    send("channel", None, path_arg, cover=cover, lyrics=False)


def main():
    a, flags = sys.argv[1:], {}
    for flag in ("--cover", "--start", "--seconds"):
        if flag in a:
            i = a.index(flag)
            flags[flag], a = (a[i + 1] if i + 1 < len(a) else "").strip(), a[:i] + a[i + 2:]
    cover = flags.get("--cover", "")
    if not a:
        print(__doc__); sys.exit(1)
    cmd = a[0]
    if cmd == "make" and len(a) >= 4:
        make(a[1], a[2], a[3], cover)
    elif cmd == "snippet" and len(a) >= 2:
        snippet(a[1], a[2] if len(a) > 2 and a[2] else 25, a[3] if len(a) > 3 else None)
    elif cmd == "channel" and len(a) >= 2:
        send("channel", None, a[1], a[2] if len(a) > 2 else "", cover)
    elif cmd == "lyrics" and len(a) >= 3:
        lyrics_comment(a[1], a[2])
    elif cmd == "release" and len(a) >= 3:
        release(a[1], a[2], cover, flags.get("--start") or None, flags.get("--seconds") or None)
    elif cmd in ("chat", "comment") and len(a) >= 3:
        try:
            target = int(a[1])
        except ValueError:
            fail("номер чата или комментария — число")
        send(cmd, target, a[2], a[3] if len(a) > 3 else "", cover)
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
