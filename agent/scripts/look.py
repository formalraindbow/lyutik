#!/usr/bin/env python3
"""Посмотреть картинку глазами модели со зрением.

    look.py <путь к картинке от корня репозитория> ["<вопрос>"]

Без вопроса — описание: что изображено, детали, надписи, настроение. Картинки от людей приходят в сообщениях
с пометкой «[фото: путь]» — туда и смотреть. Модель — GLM_VISION_MODEL из secrets.env (по умолчанию glm-4.6v),
ключ и адрес те же, что у основной модели.
"""
import base64, json, mimetypes, sys, urllib.error, urllib.request
from _common import DRY, ROOT, secrets

MAX_BYTES = 8_000_000
DEFAULT_QUESTION = "Опиши, что на картинке: что изображено, детали, надписи, настроение. По-русски, без вступлений."


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def ask(base, key, model, mime, b64, question, thinking):
    body = {"model": model, "max_tokens": 1500, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}, {"type": "text", "text": question}]}]}
    if thinking is not None:
        body["thinking"] = {"type": thinking}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())

def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        fail('использование: look.py <путь к картинке> ["<вопрос>"]')
    p = (ROOT / sys.argv[1].strip().lstrip("/")).resolve()
    if ROOT not in p.parents or not p.is_file():
        fail(f"нет такого файла в репозитории: {sys.argv[1]}")
    mime = mimetypes.guess_type(p.name)[0] or ""
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        fail("картинка — jpg, png или webp")
    if p.stat().st_size > MAX_BYTES:
        fail("картинка больше 8 МБ")
    question = sys.argv[2].strip() if len(sys.argv) > 2 and sys.argv[2].strip() else DEFAULT_QUESTION
    if DRY:
        print(f"[сухой прогон] картинку {p.name} не смотрел; вопрос: {question[:60]}")
        return
    s = secrets()
    key, base = s.get("GLM_API_KEY"), (s.get("GLM_BASE_URL") or "").rstrip("/")
    if not key or not base:
        fail("нет ключа модели — запусти tools/keys.py")
    model = s.get("GLM_VISION_MODEL") or "glm-4.6v"
    b64 = base64.b64encode(p.read_bytes()).decode()
    try:
        try:
            d = ask(base, key, model, mime, b64, question, "disabled")   # без раздумий: быстрее и дешевле
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            d = ask(base, key, model, mime, b64, question, None)         # параметр не понят — обычным путём
    except urllib.error.HTTPError as e:
        fail(f"модель ответила {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        fail(f"нет связи с моделью: {e.reason}")
    m = d["choices"][0]["message"]
    text = (m.get("content") or "").strip() or (m.get("reasoning_content") or "").strip()
    if not text:
        fail("модель посмотрела и промолчала — попробуй ещё раз")
    print(text)


if __name__ == "__main__":
    main()
