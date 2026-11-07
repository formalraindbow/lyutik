#!/usr/bin/env python3
"""Песни с настоящим вокалом: сочинить, нарезать сниппет, выпустить.

    song.py make "<название>" "<звук>" "<текст песни>"
    song.py snippet <путь к песне> [секунд] [с какой секунды]
    song.py channel <путь> ["<подпись>"]
    song.py chat <номер чата> <путь> ["<подпись>"]
    song.py comment <номер комментария> <путь> ["<подпись>"]

make — песня через Mureka (шлюз NanoGPT, ключ LLM_API_KEY): текст в разметке [Verse] / [Chorus], звук — пара фраз
по-английски (жанр, инструменты, темп). Голос — свой клон (MUREKA_VOCAL_ID в secrets.env, модель mureka/api/generate/song);
без него — голос модели по умолчанию (Mureka V9.5). Генерация идёт минуту-две. Песня длиннее SONG_MAX_SECONDS (по умолчанию 90)
обрезается с затуханием. За сутки — не больше SONG_DAILY_LIMIT песен (по умолчанию 3): каждая стоит денег.
Песни лежат в agent/lyutik/media/songs/, список — state/songs.jsonl.
snippet — кусок песни, по умолчанию 25 секунд с трети записи, с мягким входом и затуханием; ложится рядом с песней.
channel / chat / comment — отправить песню или сниппет как музыкальный трек: исполнитель «Лютик», название из списка песен.
"""
import json, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path
from _common import DRY, HOME, ROOT, STATE, append_jsonl, now, read_jsonl, secrets, tg_link

SONGS_DIR = HOME / "media" / "songs"
SONGS, POSTS, DIALOGS = STATE / "songs.jsonl", STATE / "posts.jsonl", STATE / "dialogs.jsonl"
MODEL, COST = "mureka-ai/mureka-v9.5/generate-song", 0.225          # без своего голоса
VOICE_MODEL, VOICE_COST = "mureka/api/generate/song", 0.15           # со своим голосом: vocal_id видит только эта обёртка
API = "https://api.nano-gpt.com/api"
UA = "Mozilla/5.0 (Macintosh) lyutik/1.0"
MAX_LINES = 16


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

def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], capture_output=True, text=True)
    if r.returncode:
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

def make(title, sound, lyrics):
    title, sound, lyrics = title.strip(), sound.strip(), lyrics.strip()
    if not title or not sound or not lyrics:
        fail("нужны название, звук и текст")
    sung = [l for l in lyrics.splitlines() if l.strip() and not re.match(r"^\s*\[.*\]\s*$", l)]
    if len(sung) > MAX_LINES:
        fail(f"в тексте {len(sung)} строк — на полторы минуты хватает куплета и припева, до {MAX_LINES} строк")
    t = now()
    today = [s for s in songbook() if s.get("at", "")[:10] == t.date().isoformat()]
    limit = setting("SONG_DAILY_LIMIT", 3)
    if len(today) >= limit:
        fail(f"сегодня уже {len(today)} песен из {limit} — следующая завтра")
    SONGS_DIR.mkdir(parents=True, exist_ok=True)
    out = SONGS_DIR / f"{t:%Y-%m-%d_%H%M}_{slug(title)}.mp3"
    if DRY:
        ffmpeg("-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "60", "-q:a", "9", out)
        append_jsonl(SONGS, {"at": t.isoformat(timespec="seconds"), "title": title, "sound": sound, "lyrics": lyrics,
                             "path": rel(out), "seconds": 60, "dry": True})
        print(f"[сухой прогон] песня «{title}» не заказана, вместо неё тишина 60 с: {rel(out)}")
        return
    vocal = secrets().get("MUREKA_VOCAL_ID")
    model, cost = (VOICE_MODEL, VOICE_COST) if vocal else (MODEL, COST)
    body = {"model": model, "input": sound, "prompt": sound, "lyrics": lyrics, "response_format": "mp3"}
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
    seconds, cut = seconds_of(full), setting("SONG_MAX_SECONDS", 90)
    if seconds > cut:
        ffmpeg("-i", full, "-t", cut, "-af", f"afade=t=out:st={cut - 4}:d=4", "-b:a", "192k", out)
        seconds = cut
    else:
        full.rename(out)
    append_jsonl(SONGS, {"at": t.isoformat(timespec="seconds"), "title": title, "sound": sound, "lyrics": lyrics,
                         "path": rel(out), "seconds": round(seconds), "cost": job.get("cost", cost), "run": run,
                         "voice": vocal or "по умолчанию"})
    print(f"песня «{title}» готова: {rel(out)}, {seconds:.0f} с, ${job.get('cost', cost)}"
          f"{', своим голосом' if vocal else ''}. "
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

def tg(token, method, data, files):
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
        fail(f"Telegram ответил {e.code}: {json.loads(e.read().decode() or '{}').get('description', '')}")
    except urllib.error.URLError as e:
        fail(f"нет связи с Telegram: {e.reason}")

def send(where, target, path_arg, caption=""):
    src = local(path_arg)
    e = entry_for(src)
    title = e.get("title") or src.stem
    is_snippet = ".snippet_" in src.name
    seconds = seconds_of(src)
    t = now()
    if DRY:
        print(f"[сухой прогон] {'сниппет' if is_snippet else 'песня'} «{title}», {seconds:.0f} с — не отправлено")
        return
    s = secrets()
    token, channel = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not channel:
        fail("нет ключей бота или канала — запусти tools/keys.py")
    data = {"title": title + (" (кусок)" if is_snippet else ""), "performer": "Лютик", "duration": int(seconds)}
    if caption.strip():
        data["caption"] = caption.strip()[:1000]
    files = {"audio": (src.name, src.read_bytes(), "audio/mpeg")}
    label = f"🎵 «{title}»" + (" — кусок" if is_snippet else "") + (f"\n{caption.strip()}" if caption.strip() else "")
    if where == "channel":
        msg = tg(token, "sendAudio", {**data, "chat_id": channel}, files)
        link = tg_link(msg)
        append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": label, "link": link,
                             "message_id": msg["message_id"], "format": "song", "seconds": int(seconds), "file": rel(src)})
        append_jsonl(SONGS, {"at": t.isoformat(timespec="seconds"), "title": title, "posted": link, "file": rel(src)})
        print(f"выпущено в канал {t:%d.%m %H:%M}: «{title}», {seconds:.0f} с — {link}")
    elif where == "chat":
        tg(token, "sendAudio", {**data, "chat_id": target}, files)
        append_jsonl(DIALOGS, {"at": t.isoformat(timespec="seconds"), "dir": "out", "kind": "dm", "chat": target,
                               "text": label, "voice": True})
        print(f"отправлено в личку {target}: «{title}», {seconds:.0f} с")
    else:
        group = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getChat", data=json.dumps({"chat_id": channel}).encode(),
            headers={"Content-Type": "application/json"}), timeout=30).read().decode())["result"].get("linked_chat_id")
        if not group:
            fail("у канала нет группы обсуждения")
        msg = tg(token, "sendAudio", {**data, "chat_id": group,
                 "reply_parameters": json.dumps({"message_id": target, "allow_sending_without_reply": True})}, files)
        post = next((d.get("post") for d in read_jsonl(DIALOGS)
                     if d.get("kind") == "comment" and d.get("message_id") == target), None)
        append_jsonl(DIALOGS, {"at": t.isoformat(timespec="seconds"), "dir": "out", "kind": "comment", "post": post,
                               "chat": group, "reply_to": target, "message_id": msg["message_id"], "text": label, "voice": True})
        print(f"отправлено в ответ на №{target}: «{title}», {seconds:.0f} с")


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); sys.exit(1)
    cmd = a[0]
    if cmd == "make" and len(a) >= 4:
        make(a[1], a[2], a[3])
    elif cmd == "snippet" and len(a) >= 2:
        snippet(a[1], a[2] if len(a) > 2 and a[2] else 25, a[3] if len(a) > 3 else None)
    elif cmd == "channel" and len(a) >= 2:
        send("channel", None, a[1], a[2] if len(a) > 2 else "")
    elif cmd in ("chat", "comment") and len(a) >= 3:
        try:
            target = int(a[1])
        except ValueError:
            fail("номер чата или комментария — число")
        send(cmd, target, a[2], a[3] if len(a) > 3 else "")
    else:
        print(__doc__); sys.exit(1)


if __name__ == "__main__":
    main()
