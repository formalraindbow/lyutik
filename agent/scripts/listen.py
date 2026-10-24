#!/usr/bin/env python3
"""Послушать звук: файл из репозитория или ссылку.

    listen.py <путь к файлу от корня репозитория | ссылка http(s)://…> [--no-words]

Файлы от людей приходят в сообщениях с пометкой «[звук: путь]». Ссылка (YouTube, SoundCloud и другие, что
понимает yt-dlp) скачивается в agent/lyutik/media/in/ и слушается оттуда. Сам слух — в _listen.py под
окружением EARS_PYTHON (secrets.env): темп, тональность, громкость по времени, части, слова как спеты.
Занимает минуты: Whisper разбирает слова на процессоре.
"""
import re, subprocess, sys
from pathlib import Path
from _common import DRY, HOME, ROOT, secrets

MEDIA_IN = HOME / "media" / "in"
EARS = Path(__file__).resolve().parent / "_listen.py"
MAX_MB = 60


def fail(msg):
    print(f"не послушал: {msg}")
    sys.exit(1)

def python_for_ears():
    s = secrets()
    py = s.get("EARS_PYTHON") or str(Path.home() / ".local" / "share" / "lyutik" / "ears" / "bin" / "python")
    if not Path(py).exists():
        fail(f"нет окружения для слуха: {py} (EARS_PYTHON в secrets.env)")
    return py

def fetch(url, py):
    """Ссылка → mp3 в media/in через yt-dlp из того же окружения."""
    MEDIA_IN.mkdir(parents=True, exist_ok=True)
    ytdlp = Path(py).parent / "yt-dlp"
    if not ytdlp.exists():
        fail("в окружении для слуха нет yt-dlp")
    tmpl = str(MEDIA_IN / "link_%(id)s.%(ext)s")
    r = subprocess.run([str(ytdlp), "--no-playlist", "-x", "--audio-format", "mp3", "--audio-quality", "5",
                        "--max-filesize", f"{MAX_MB}m", "-o", tmpl, "--print", "after_move:filepath", "--no-warnings", url],
                       capture_output=True, text=True, timeout=240)
    if r.returncode != 0:
        fail(f"не скачалось: {r.stderr.strip()[-200:] or r.stdout.strip()[-200:]}")
    path = Path(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else None
    if not path or not path.exists():
        fail("скачалось, но файл не найден")
    return path

HEAR_MODEL, HEAR_SECONDS = "ag/gemini-2.5-flash", 300

def hear(path, measured):
    """Слух модели: как это звучит — инструменты, голос, настроение, что происходит по времени. Через anymodel,
    если есть ключ; звук ужимается до 32 кбит/с моно и первых HEAR_SECONDS секунд. Нет ключа или сбой — пусто."""
    import base64, json, tempfile, urllib.error, urllib.request
    s = secrets()
    key, base = s.get("ANYMODEL_API_KEY"), (s.get("ANYMODEL_BASE_URL") or "").rstrip("/")
    if not key or not base:
        return ""
    tmp = Path(tempfile.mkdtemp(prefix="lyutik-hear-")) / "clip.mp3"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(HEAR_SECONDS), "-i", str(path), "-ac", "1", "-b:a", "32k", str(tmp)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return ""
    b64 = base64.b64encode(tmp.read_bytes()).decode()
    prompt = ("Послушай запись и опиши по-русски, что слышно, — только то, что действительно звучит. "
              "1) Инструменты и звуки, 2) голос: есть ли, мужской или женский, манера, 3) настроение и энергия, 4) жанр или на что похоже, "
              "5) что происходит по времени — вступление, куплеты, припев, перемены, с примерным временем, 6) первые строки слов, если разборчивы. "
              "Коротко, по пунктам, без вступлений. Измеренное прибором, чтобы не спорить с ним: " + " ".join(measured)[:300])
    body = {"model": s.get("HEAR_MODEL") or HEAR_MODEL, "max_tokens": 700, "messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}}, {"type": "text", "text": prompt}]}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh) lyutik/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            text = json.loads(resp.read().decode())["choices"][0]["message"].get("content") or ""
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
        return f"Слух модели не ответил: {type(e).__name__}"
    return "Как звучит (слух модели, слушала первые {} минут):\n{}".format(HEAR_SECONDS // 60, text.strip()) if text.strip() else ""

def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        fail("использование: listen.py <файл | ссылка> [--no-words]")
    target = sys.argv[1].strip()
    want_words = "--no-words" not in sys.argv[2:]
    if DRY:
        print(f"[сухой прогон] не слушал: {target[:80]}")
        return
    py = python_for_ears()
    if re.match(r"https?://", target):
        path = fetch(target, py)
    else:
        path = (ROOT / target.lstrip("/")).resolve()
        if ROOT not in path.parents or not path.is_file():
            fail(f"нет такого файла в репозитории: {target}")
    if path.stat().st_size > MAX_MB * 1024 * 1024:
        fail(f"файл больше {MAX_MB} МБ")
    r = subprocess.run([py, str(EARS), str(path)] + ([] if want_words else ["--no-words"]),
                       capture_output=True, text=True, timeout=560)
    if r.returncode != 0:
        fail((r.stderr or r.stdout).strip()[-300:])
    report = r.stdout.strip()
    heard = hear(path, [l for l in report.splitlines()[:3]])
    rel = path.relative_to(ROOT) if ROOT in path.parents else path
    print(report + ("\n" + heard if heard else "") + f"\n(файл: {rel})")


if __name__ == "__main__":
    main()
