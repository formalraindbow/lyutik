#!/usr/bin/env python3
"""Спеть: голосовое сообщение с лютней — в канал, в личку или в ветку комментариев.

    voice.py channel "<текст>" ["<мелодия>"] [диктор] ["<подпись>"] [--key "соль минор"] [--bpm 86] [--repeats 2] [--bass]
    voice.py chat <номер чата> "<текст>" ["<мелодия>"] [диктор] ["<подпись>"] [флаги]
    voice.py comment <номер комментария> "<текст>" ["<мелодия>"] [диктор] ["<подпись>"] [флаги]

Подпись по умолчанию — первая строка текста; для одной лютни — то, что передано отдельно.

Текст читается голосом (dmitry — по умолчанию, svetlana; или местные дикторы 0–4), лютня играет мелодию: ноты и аккорды
через пробел — «A3 C4 E4:2 - G3+B3+D4:3», «перебор Am F C G», «бой Gm3:4 Eb3:4 Bb3 F3». «:доли» — длительность в четвертях
(нота — 1, аккорд — 4), «-» пауза, «+» несколько нот разом. Аккорд — буква, # или b, вид (ничего — мажор; m, 7, m7, maj7,
dim, sus2, sus4, aug) и необязательная октава корня. Слова «перебор» (по умолчанию), «арпеджио», «бой» задают манеру
для аккордов с этого места, «с басом» — корень октавой ниже под каждым аккордом. Последний аккорд тянется.
Флаги, в любом месте после команды: --key — тональность («соль минор», «C major», «Gm»): без мелодии лютня играет
стандартный ход в ней (минор i–VI–III–VII, мажор I–V–vi–IV); --bpm — темп в ударах в минуту (по умолчанию 96);
--repeats — сколько раз прокрутить ход или мелодию (ход — 2, своя мелодия — 1; не больше 8); --bass — то же, что «с басом».
Без мелодии и тональности лютня перебирает Am–F–C–G, как прежде. Пустой текст с мелодией или тональностью — только лютня.
Синтез идёт в окружении VOICE_PYTHON (secrets.env), звук уходит как OGG/Opus. Получается голосовое сообщение Telegram.
"""
import json, os, subprocess, sys, tempfile, urllib.error, urllib.request, uuid
from pathlib import Path
from _common import DRY, STATE, append_jsonl, now, read_jsonl, secrets, tg_link

MAX_TEXT = 3000
POSTS, DIALOGS = STATE / "posts.jsonl", STATE / "dialogs.jsonl"
SING = Path(__file__).resolve().parent / "_sing.py"


def fail(msg):
    print(f"не спето: {msg}")
    sys.exit(1)

def api(token, method, data, files):
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
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode())["result"]
    except urllib.error.HTTPError as e:
        fail(f"Telegram ответил {e.code}: {json.loads(e.read().decode() or '{}').get('description', '')}")
    except urllib.error.URLError as e:
        fail(f"нет связи с Telegram: {e.reason}")

def flags_of(argv):
    """Позиционные аргументы отдельно от флагов --key, --bpm, --repeats, --bass (и вида --key=…)."""
    args, flags, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and len(a) > 2:
            name, eq, val = a[2:].partition("=")
            if name == "bass":
                flags["bass"] = True
            elif name in ("key", "bpm", "repeats"):
                if not eq:
                    i += 1
                    if i == len(argv):
                        fail(f"--{name} без значения")
                    val = argv[i]
                flags[name] = val
            else:
                fail(f"неизвестный флаг --{name}; есть --key, --bpm, --repeats, --bass")
        else:
            args.append(a)
        i += 1
    return args, flags

def make(text, melody, speaker, extra=()):
    """WAV через VOICE_PYTHON, потом OGG/Opus через ffmpeg. Возвращает (ogg-байты, секунды, что сыграла лютня)."""
    s = secrets()
    py = s.get("VOICE_PYTHON") or str(Path.home() / "Desktop" / "nettyan" / ".venv" / "bin" / "python")
    if not Path(py).exists():
        fail(f"нет окружения для голоса: {py} (VOICE_PYTHON в secrets.env)")
    tmp = Path(tempfile.mkdtemp(prefix="lyutik-voice-"))
    wav, ogg = tmp / "mix.wav", tmp / "voice.ogg"
    env = {**os.environ, **{k: v for k, v in s.items() if k.startswith(("OPENROUTER_", "ELEVEN_"))}}   # ключи голоса — только в окружение синтеза
    r = subprocess.run([py, str(SING), str(wav), str(speaker), text, melody, *extra], capture_output=True, text=True, timeout=110, env=env)
    if r.returncode != 0:
        fail(f"синтез не вышел: {(r.stderr or r.stdout).strip()[-300:]}")
    lines = r.stdout.strip().splitlines()
    seconds = float(lines[-1])
    played = next((l for l in lines if l.startswith("лютня:")), "")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "48k", "-vbr", "on", str(ogg)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        fail(f"ffmpeg: {r.stderr.strip()[-200:]}")
    return ogg.read_bytes(), seconds, played

def main():
    a, flags = flags_of(sys.argv[1:])
    if not a or a[0] not in ("channel", "chat", "comment"):
        print(__doc__); sys.exit(1)
    where = a[0]
    try:
        target = int(a[1]) if where != "channel" else None
    except (IndexError, ValueError):
        fail("номер чата или комментария — число")
    rest = a[1:] if where == "channel" else a[2:]
    text = rest[0].strip() if rest else ""
    melody = rest[1].strip() if len(rest) > 1 else ""
    speaker = (rest[2].strip().lower() if len(rest) > 2 and str(rest[2]).strip() else (secrets().get("VOICE_DEFAULT") or "dmitry").lower())
    if speaker not in ("dmitry", "svetlana", "0", "1", "2", "3", "4", "george", "adam", "daniel", "brian", "callum", "charlie",
                       "chris", "eric", "harry", "liam", "roger", "will", "bill", "sarah", "alice", "bella", "jessica", "laura",
                       "lily", "matilda", "river"):
        fail("голос — имя ElevenLabs (george, adam, daniel…), dmitry, svetlana или номер местного диктора 0–4")
    if not text and not melody and not flags:
        fail("нужен текст, мелодия или тональность (--key)")
    if len(text) > MAX_TEXT:
        fail(f"текст длиннее {MAX_TEXT} знаков ({len(text)}) — спой короче или в два приёма")
    try:
        if "bpm" in flags and not 30 <= float(flags["bpm"]) <= 300:
            fail("--bpm — темп от 30 до 300 ударов в минуту")
        if "repeats" in flags and not 1 <= int(flags["repeats"]) <= 8:
            fail("--repeats — целое от 1 до 8")
    except ValueError:
        fail("--bpm и --repeats — числа")
    extra = [f"--{k}={flags[k]}" for k in ("key", "bpm", "repeats") if flags.get(k)] + (["--bass"] if flags.get("bass") else [])
    ogg, seconds, played = make(text, melody, speaker, extra)
    note = f"\n{played}" if played else ""
    caption = (rest[3].strip() if len(rest) > 3 else "") or text.split("\n", 1)[0][:300]
    t = now()
    if DRY:
        out = STATE / "voice.dry.ogg"
        out.write_bytes(ogg)
        print(f"[сухой прогон] спето {seconds:.0f} с, не отправлено; файл {out}{note}")
        return
    s = secrets()
    token, channel = s.get("TELEGRAM_BOT_TOKEN"), s.get("TELEGRAM_CHANNEL_ID")
    if not token or not channel:
        fail("нет ключей бота или канала — запусти tools/keys.py")
    files = {"voice": ("lyutik.ogg", ogg, "audio/ogg")}
    data = {"duration": int(seconds)}
    if caption:
        data["caption"] = caption
    if where == "channel":
        msg = api(token, "sendVoice", {**data, "chat_id": channel}, files)
        link = tg_link(msg)
        append_jsonl(POSTS, {"at": t.isoformat(timespec="seconds"), "text": "🎙 " + (text or "(лютня)"), "link": link,
                             "message_id": msg["message_id"], "format": "voice", "seconds": int(seconds)})
        print(f"спето в канал {t:%d.%m %H:%M}, {seconds:.0f} с: {link}{note}")
    elif where == "chat":
        msg = api(token, "sendVoice", {**data, "chat_id": target}, files)
        append_jsonl(DIALOGS, {"at": t.isoformat(timespec="seconds"), "dir": "out", "kind": "dm", "chat": target,
                               "text": "🎙 " + (text or "(лютня)"), "voice": True})
        print(f"спето в личку {target}, {seconds:.0f} с{note}")
    else:
        group = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getChat", data=json.dumps({"chat_id": channel}).encode(),
            headers={"Content-Type": "application/json"}), timeout=30).read().decode())["result"].get("linked_chat_id")
        if not group:
            fail("у канала нет группы обсуждения")
        msg = api(token, "sendVoice", {**data, "chat_id": group,
                  "reply_parameters": json.dumps({"message_id": target, "allow_sending_without_reply": True})}, files)
        post = next((d.get("post") for d in read_jsonl(DIALOGS)
                     if d.get("kind") == "comment" and d.get("message_id") == target), None)
        append_jsonl(DIALOGS, {"at": t.isoformat(timespec="seconds"), "dir": "out", "kind": "comment", "post": post,
                               "chat": group, "reply_to": target, "message_id": msg["message_id"],
                               "text": "🎙 " + (text or "(лютня)"), "voice": True})
        print(f"спето в ответ на №{target}, {seconds:.0f} с{note}")


if __name__ == "__main__":
    main()
