"""Mureka напрямую (api.mureka.ai), ключ MUREKA_API_KEY в secrets.env.

Цены — официальный прайс 8.11: песня по тексту на mureka-9.5 — $0.15 за песню (за запрос по умолчанию 2 песни),
клон голоса — $5, загрузка файла — бесплатно. Через NanoGPT та же песня своим голосом стоила $0.5.
Песня приходит с lyrics_sections: строки и слова с таймкодами — по ним идут субтитры сниппета.
vocal_id привязан к аккаунту: клон, сделанный через NanoGPT, здесь не принимается.
"""
import json, time, urllib.error, urllib.request, uuid
from pathlib import Path
from _common import secrets

BASE = "https://api.mureka.ai"
SONG_PRICE = {"mureka-9.5": 0.15, "mureka-9": 0.045, "mureka-8": 0.045, "mureka-o2": 0.045, "mureka-7.6": 0.03}


class MurekaError(Exception):
    pass


def key():
    k = secrets().get("MUREKA_API_KEY")
    if not k:
        raise MurekaError("нет MUREKA_API_KEY в secrets.env")
    return k

def _call(method, path, body=None, form=None, timeout=120):
    headers = {"Authorization": f"Bearer {key()}", "User-Agent": "lyutik/1.0"}
    data = None
    if form is not None:                                   # multipart: поля и файлы
        b = uuid.uuid4().hex
        parts = []
        for name, value in form.items():
            if isinstance(value, Path):
                parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{value.name}\"\r\n"
                             f"Content-Type: application/octet-stream\r\n\r\n".encode() + value.read_bytes() + b"\r\n")
            else:
                parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        data = b"".join(parts) + f"--{b}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise MurekaError(f"Mureka ответила {e.code}: {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MurekaError(f"нет связи с Mureka: {getattr(e, 'reason', e)}")

def generate(lyrics, prompt="", vocal_id="", melody_id="", n=2, model="mureka-9.5", gender=""):
    """Заказ песни; возвращает номер задачи. melody_id ни с чем не совмещается (так в документации)."""
    body = {"lyrics": lyrics, "model": model, "n": n}
    if melody_id:
        body["melody_id"] = melody_id
    else:
        if prompt:
            body["prompt"] = prompt[:1024]
        if vocal_id:
            body["vocal_id"] = vocal_id
    if gender:
        body["gender"] = gender
    task = _call("POST", "/v1/song/generate", body)
    if not task.get("id"):
        raise MurekaError(f"Mureka не дала номер задачи: {json.dumps(task, ensure_ascii=False)[:200]}")
    return task["id"]

def wait(task_id, limit=480, step=6):
    """Ждёт задачу; возвращает choices — песни с url, duration (мс) и lyrics_sections."""
    t0 = time.time()
    while time.time() - t0 < limit:
        time.sleep(step)
        st = _call("GET", f"/v1/song/query/{task_id}")
        status = st.get("status")
        if status == "succeeded":
            return st.get("choices") or []
        if status in ("failed", "timeouted", "cancelled"):
            raise MurekaError(f"Mureka не спела ({status}): {st.get('failed_reason') or ''}")
    raise MurekaError(f"песня не готова за {limit // 60} мин; задача {task_id}")

def lines_of(choice):
    """Строки песни с таймкодами в секундах — как их спели, по lyrics_sections."""
    out = []
    for sec in choice.get("lyrics_sections") or []:
        for ln in sec.get("lines") or []:
            if (ln.get("text") or "").strip() and ln.get("end") is not None:
                out.append({"line": ln["text"].strip(), "start": round(ln["start"] / 1000, 2), "end": round(ln["end"] / 1000, 2)})
    return out

def upload(path, purpose):
    """Файл для генерации: purpose — melody (mp3, m4a или mid, 5–60 с), reference (30 с), voice (5–15 с)."""
    res = _call("POST", "/v1/files/upload", form={"purpose": purpose, "file": Path(path)}, timeout=180)
    if not res.get("id"):
        raise MurekaError(f"загрузка без номера: {json.dumps(res, ensure_ascii=False)[:200]}")
    return res["id"]

def clone(path, description=""):
    """Клон голоса из чистого образца 15–30 с (mp3 или m4a, до 10 МБ); $5. Возвращает vocal_id."""
    res = _call("POST", "/v1/song/vocal-clone", form={"description": description[:1024], "file": Path(path)}, timeout=300)
    if not res.get("vocal_id"):
        raise MurekaError(f"клон без vocal_id: {json.dumps(res, ensure_ascii=False)[:200]}")
    return res["vocal_id"]

def billing():
    return _call("GET", "/v1/account/billing")
