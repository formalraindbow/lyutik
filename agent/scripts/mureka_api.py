"""Mureka напрямую (api.mureka.ai), ключ MUREKA_API_KEY в secrets.env.

Цены — официальный прайс 8.11: песня по тексту на mureka-9.5 — $0.15 за песню (за запрос по умолчанию 2 песни),
клон голоса — $5, загрузка файла — бесплатно. Через NanoGPT та же песня своим голосом стоила $0.5.
Песня приходит с lyrics_sections: строки и слова с таймкодами — по ним идут субтитры сниппета.
vocal_id привязан к аккаунту: клон, сделанный через NanoGPT, здесь не принимается.

Та же Mureka через fal.ai (14.11): MUREKA_BACKEND=fal и FAL_KEY в secrets.env. Trial на api.mureka.ai разовый,
следующий тариф $1 000, а fal берёт по факту: песня 9.5 — $0.15 за штуку (один запрос — одна песня), клон — $5,
разбор — $0.1, правка участка — $0.1. Ответы fal того же вида, что у Mureka (audio.url, song_id, lyrics_sections
в мс), поэтому снаружи модуль не меняется: пути /v1/... переводятся на приложения fal здесь же. Клон и song_id
живут в аккаунте fal, со старым аккаунтом Mureka не смешиваются.
"""
import base64, json, time, urllib.error, urllib.request, uuid
from pathlib import Path
from _common import secrets

BASE = "https://api.mureka.ai"
SONG_PRICE = {"mureka-9.5": 0.15, "mureka-9": 0.045, "mureka-8": 0.045, "mureka-o2": 0.045, "mureka-7.6": 0.03}
FAL_QUEUE, FAL_RUN = "https://queue.fal.run", "https://fal.run"
FAL_APP = {"generate": "mureka/api/generate/song", "edit": "mureka/api/song/edit", "describe": "mureka/api/song/describe",
           "clone": "mureka/api/voice/clone", "upload": "mureka/api/files/upload",
           "remix:mureka-9.5": "mureka/v9.5/song/remix", "remix:mureka-8": "mureka/v8/song/remix"}
FAL_TASK = "fal:"          # номер задачи fal: префикс + JSON со ссылками статуса и ответа каждой песни
FAL_SONG = "fal:"          # song_id песни, спетой на fal: так studio отличает его от номера старого аккаунта Mureka


class MurekaError(Exception):
    pass


def backend():
    return (secrets().get("MUREKA_BACKEND") or "mureka").strip().lower()

def key():
    k = secrets().get("MUREKA_API_KEY")
    if not k:
        raise MurekaError("нет MUREKA_API_KEY в secrets.env")
    return k

def _data_uri(path):
    p = Path(path)
    kind = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".mid": "audio/midi"}.get(p.suffix.lower(), "application/octet-stream")
    return f"data:{kind};base64," + base64.b64encode(p.read_bytes()).decode()

def _fal(url, body=None, timeout=120):
    k = secrets().get("FAL_KEY")
    if not k:
        raise MurekaError("нет FAL_KEY в secrets.env")
    headers = {"Authorization": f"Key {k}", "User-Agent": "lyutik/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise MurekaError(f"fal ответил {e.code}: {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MurekaError(f"нет связи с fal: {getattr(e, 'reason', e)}")

def _fal_submit(app, body):
    """Заказ в очередь fal; возвращает [ссылка статуса, ссылка ответа]."""
    r = _fal(f"{FAL_QUEUE}/{app}", {k: v for k, v in body.items() if v not in (None, "")})
    if not r.get("status_url") or not r.get("response_url"):
        raise MurekaError(f"fal не дал номер задачи: {json.dumps(r, ensure_ascii=False)[:200]}")
    return [r["status_url"], r["response_url"]]

def _fal_task(*jobs):
    return FAL_TASK + json.dumps(list(jobs))

def _fal_choice(out):
    """Ответ fal → песня в виде, как её отдаёт Mureka в choices."""
    audio = out.get("audio") or {}
    return {"id": FAL_SONG + out["song_id"] if out.get("song_id") else None, "url": audio.get("url"),
            "duration": out.get("duration"), "lyrics_sections": out.get("lyrics_sections") or []}

def _fal_call(method, path, body=None, form=None, timeout=120):
    """Пути Mureka /v1/... → приложения fal. Задачи с ожиданием отдают {"id": "fal:…"} — его понимает wait()."""
    body = dict(body or {})
    if path == "/v1/song/describe":
        out = _fal(f"{FAL_RUN}/{FAL_APP['describe']}", {"audio_url": body.get("url")}, timeout=timeout)
        return {"instrument": out.get("instruments") or [], "genres": out.get("genres") or [],
                "tags": out.get("tags") or [], "description": out.get("description") or ""}
    if path == "/v1/song/region-edit":
        return {"id": _fal_task(_fal_submit(FAL_APP["edit"], _fal_source(body)))}
    if path == "/v1/song/remix":
        app = FAL_APP.get(f"remix:{body.pop('model', 'mureka-9.5')}") or FAL_APP["remix:mureka-9.5"]
        return {"id": _fal_task(_fal_submit(app, _fal_source(body)))}
    if path == "/v1/account/billing":
        return _fal("https://api.fal.ai/v1/account/billing?expand=credits")
    raise MurekaError(f"путь {path} через fal не переведён")

def _fal_source(body):
    """song_id песни, спетой на fal, — без префикса; номер со старого аккаунта Mureka fal не примет."""
    sid = body.get("song_id") or ""
    if sid and not sid.startswith(FAL_SONG):
        raise MurekaError("эта песня спета на старом аккаунте Mureka — правь загрузкой файла, а не song_id")
    if sid:
        body["song_id"] = sid[len(FAL_SONG):]
    return body

def _call(method, path, body=None, form=None, timeout=120):
    if backend() == "fal":
        return _fal_call(method, path, body, form, timeout)
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
    for attempt in range(4):                               # Mureka держит один запрос за раз: 429 «concurrent» — подождать
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code == 429 and "concurrent" in text.lower() and attempt < 3:
                time.sleep(20 * (attempt + 1))
                continue
            raise MurekaError(f"Mureka ответила {e.code}: {text[:300]}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MurekaError(f"нет связи с Mureka: {getattr(e, 'reason', e)}")

def generate(lyrics, prompt="", vocal_id="", melody_id="", n=2, model="mureka-9.5", gender=""):
    """Заказ песни; возвращает номер задачи. melody_id ни с чем не совмещается (так в документации) и русский
    текст не берёт: 400 «melody_id is not supported under the language of the input lyrics» (проба 8.11)."""
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
    if backend() == "fal":                                 # на fal один запрос — одна песня: n заказов в очередь
        one = {k: v for k, v in body.items() if k != "n"}
        return _fal_task(*[_fal_submit(FAL_APP["generate"], one) for _ in range(max(1, n))])
    task = _call("POST", "/v1/song/generate", body)
    if not task.get("id"):
        raise MurekaError(f"Mureka не дала номер задачи: {json.dumps(task, ensure_ascii=False)[:200]}")
    return task["id"]

def wait(task_id, limit=480, step=6):
    """Ждёт задачу; возвращает choices — песни с url, duration (мс) и lyrics_sections."""
    if str(task_id).startswith(FAL_TASK):
        return _fal_wait(json.loads(str(task_id)[len(FAL_TASK):]), limit, step)
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

def _fal_wait(jobs, limit, step):
    """Ждёт все песни заказа fal. Упала одна — вернём остальные; упали все — ошибка с причиной."""
    t0, left, done, errors = time.time(), list(jobs), [], []
    while left and time.time() - t0 < limit:
        time.sleep(step)
        for job in list(left):
            status_url, response_url = job
            st = _fal(status_url).get("status")
            if st != "COMPLETED":
                continue
            left.remove(job)
            try:
                done.append(_fal_choice(_fal(response_url)))
            except MurekaError as e:
                errors.append(str(e))
    if done:
        return done
    if errors:
        raise MurekaError(f"Mureka на fal не спела: {errors[0]}")
    raise MurekaError(f"песня не готова за {limit // 60} мин (fal)")

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
    if backend() == "fal":
        res = _fal(f"{FAL_RUN}/{FAL_APP['upload']}", {"file_url": _data_uri(path), "purpose": purpose}, timeout=180)
        if not res.get("id"):
            raise MurekaError(f"загрузка без номера (fal): {json.dumps(res, ensure_ascii=False)[:200]}")
        return res["id"]
    res = _call("POST", "/v1/files/upload", form={"purpose": purpose, "file": Path(path)}, timeout=180)
    if not res.get("id"):
        raise MurekaError(f"загрузка без номера: {json.dumps(res, ensure_ascii=False)[:200]}")
    return res["id"]

def clone(path, description=""):
    """Клон голоса из чистого образца 15–30 с (mp3 или m4a, до 10 МБ); $5. Возвращает vocal_id."""
    if backend() == "fal":
        res = _fal(f"{FAL_RUN}/{FAL_APP['clone']}", {"audio_url": _data_uri(path), "description": description[:1024] or None}, timeout=300)
        if not res.get("vocal_id"):
            raise MurekaError(f"клон без vocal_id (fal): {json.dumps(res, ensure_ascii=False)[:200]}")
        return res["vocal_id"]
    res = _call("POST", "/v1/song/vocal-clone", form={"description": description[:1024], "file": Path(path)}, timeout=300)
    if not res.get("vocal_id"):
        raise MurekaError(f"клон без vocal_id: {json.dumps(res, ensure_ascii=False)[:200]}")
    return res["vocal_id"]

def billing():
    return _call("GET", "/v1/account/billing")
