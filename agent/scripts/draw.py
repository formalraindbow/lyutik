#!/usr/bin/env python3
"""Нарисовать картинку по описанию.

    draw.py "<описание>" [fast|good]

Картинка ложится в agent/lyutik/media/out/, печатается путь — его берут post_photo и channel_photo.
Рисует модель через anymodel (ANYMODEL_API_KEY в secrets.env): fast — быстрая, секунды; good — медленная, минута,
но заметно лучше. По умолчанию good.
"""
import base64, datetime, json, re, sys, urllib.error, urllib.request
from _common import DRY, HOME, ROOT, secrets

MODELS = {"fast": "am/flux.2-klein-4b", "good": "cx/gpt-image-2.5"}
OUT = HOME / "media" / "out"


def fail(msg):
    print(f"не нарисовал: {msg}")
    sys.exit(1)

def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        fail('использование: draw.py "<описание>" [fast|good]')
    prompt = sys.argv[1].strip()
    mode = (sys.argv[2].strip().lower() if len(sys.argv) > 2 and sys.argv[2].strip() else "good")
    if mode not in MODELS:
        fail("режим — fast или good")
    if len(prompt) > 2000:
        fail("описание длиннее 2000 знаков")
    OUT.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w]+", "_", prompt[:30], flags=re.U).strip("_") or "картинка"
    dest = OUT / f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}_{stem}.png"
    if DRY:
        print(f"[сухой прогон] не рисовал ({mode}): {prompt[:60]} → {dest.relative_to(ROOT)}")
        return
    s = secrets()
    key, base = s.get("ANYMODEL_API_KEY"), (s.get("ANYMODEL_BASE_URL") or "").rstrip("/")
    if not key or not base:
        fail("нет ключа anymodel — запусти tools/keys.py --anymodel-clipboard")
    model = s.get(f"DRAW_MODEL_{mode.upper()}") or MODELS[mode]
    body = {"model": model, "prompt": prompt, "size": "1024x1024", "n": 1}
    req = urllib.request.Request(base + "/images/generations", data=json.dumps(body, ensure_ascii=False).encode(), headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh) lyutik/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            d = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        fail(f"модель ответила {e.code}: {e.read().decode(errors='replace')[:200]}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail(f"не дождался картинки: {type(e).__name__}: {str(e)[:120]}")
    item = (d.get("data") or [{}])[0]
    if item.get("b64_json"):
        dest.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        try:
            with urllib.request.urlopen(urllib.request.Request(item["url"], headers={"User-Agent": "Mozilla/5.0"}), timeout=120) as r:
                dest.write_bytes(r.read())
        except (urllib.error.URLError, OSError) as e:
            fail(f"картинка есть, но не скачалась: {e}")
    else:
        fail(f"ответ без картинки: {str(d)[:200]}")
    print(f"нарисовано ({mode}): {dest.relative_to(ROOT)}, {dest.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
