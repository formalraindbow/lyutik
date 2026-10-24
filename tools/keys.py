"""Ключи Лютика: бот Telegram, канал, ключ GLM — по шагам, с проверкой каждого.

    python3 tools/keys.py

Пишет в ~/.config/lyutik/secrets.env (папка 700, файл 600) — вне репозитория, в git не попадает.
Ключи вводятся скрыто и на экран не выводятся, видно только начало и конец. Повторный запуск
показывает, что уже записано, и даёт заменить любой пункт. Ничего не публикует и никому не пишет.
"""
import getpass, json, os, re, subprocess, sys, time
import urllib.error, urllib.request
from pathlib import Path

SECRETS = Path.home() / ".config" / "lyutik" / "secrets.env"
TG_API = "https://api.telegram.org"
GLM_BASE = "https://api.z.ai/api/coding/paas/v4"           # Coding Plan, OpenAI-совместимый
GLM_FALLBACK = ["glm-5.3", "glm-5.2", "glm-5.1", "glm-5", "glm-4.7", "glm-4.6"]  # если /models молчит
KEYS = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME", "TELEGRAM_CHANNEL_ID", "TELEGRAM_CHANNEL_TITLE",
        "GLM_API_KEY", "GLM_BASE_URL", "GLM_MODEL", "GLM_FALLBACK_MODEL",
        "ANYMODEL_API_KEY", "ANYMODEL_BASE_URL", "VOICE_PYTHON", "EARS_PYTHON", "ELEVEN_API_KEY",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "VOICE_DEFAULT"]
SECRET_KEYS = {"TELEGRAM_BOT_TOKEN", "GLM_API_KEY", "ANYMODEL_API_KEY", "ELEVEN_API_KEY", "OPENROUTER_API_KEY"}


class Fail(Exception):
    """Понятная причина, почему шаг не прошёл."""


# ── файл ──────────────────────────────────────────────────────────────────────
def load():
    data = {}
    if SECRETS.exists():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"')
    return data

def save(data):
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS.parent, 0o700)
    tmp = SECRETS.with_suffix(".tmp")
    lines = ["# Ключи Лютика. Пишет tools/keys.py, руками править не нужно."]
    lines += [f'{k}="{data[k]}"' for k in KEYS if data.get(k)]
    lines += [f'{k}="{v}"' for k, v in data.items() if k not in KEYS and v]   # чужие строки не выбрасываем
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, SECRETS)

def mask(v):
    v = v or ""
    return "—" if not v else (v[:4] + "…" + v[-4:] if len(v) > 12 else "…")


# ── сеть ──────────────────────────────────────────────────────────────────────
def http(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "User-Agent": "lyutik-keys", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError) as e:
        host = re.sub(r"^https?://([^/]+).*", r"\1", url)
        raise Fail(f"нет связи с {host}: {getattr(e, 'reason', e)}. Проверь интернет или VPN.")

def tg(token, method, timeout=30, **params):
    code, r = http("POST", f"{TG_API}/bot{token}/{method}", params, timeout=timeout)
    if not r.get("ok"):
        raise Fail(f"Telegram ответил {code}: {r.get('description', 'без описания')}")
    return r["result"]


# ── шаг 1: бот ────────────────────────────────────────────────────────────────
def check_bot(token):
    if not re.fullmatch(r"\d+:[\w-]{30,}", token):
        raise Fail("не похоже на токен бота: он выглядит как 123456789:ABC… (цифры, двоеточие, длинная строка)")
    me = tg(token, "getMe")
    return me["username"], me["id"]

def step_bot(data):
    print("\n1/3 · Бот Telegram")
    if data.get("TELEGRAM_BOT_TOKEN"):
        print(f"  Уже записан: @{data.get('TELEGRAM_BOT_USERNAME', '?')} ({mask(data['TELEGRAM_BOT_TOKEN'])}).")
    print("  В Telegram: @BotFather → /newbot → имя (например, «Лютик») → username с окончанием _bot.\n"
          "  BotFather пришлёт токен — скопируй его.")
    while True:
        prompt = ("  Вставь токен (Cmd+V) и нажми Enter" +
                  (", или просто Enter — оставить записанный" if data.get("TELEGRAM_BOT_TOKEN") else "") +
                  ". Вставленное не видно, так и задумано: ")
        token = getpass.getpass(prompt).strip()
        if not token and data.get("TELEGRAM_BOT_TOKEN"):
            token = data["TELEGRAM_BOT_TOKEN"]
        if not token:
            continue
        try:
            username, _ = check_bot(token)
        except Fail as e:
            print(f"  ✗ {e}")
            continue
        if token != data.get("TELEGRAM_BOT_TOKEN"):
            data.pop("TELEGRAM_CHANNEL_ID", None)        # новый бот — канал проверяем заново
            data.pop("TELEGRAM_CHANNEL_TITLE", None)
        data["TELEGRAM_BOT_TOKEN"], data["TELEGRAM_BOT_USERNAME"] = token, username
        save(data)
        print(f"  ✓ бот @{username} отвечает, токен записан ({mask(token)})")
        return


# ── шаг 2: канал ──────────────────────────────────────────────────────────────
def channels_from_updates(token):
    """Каналы, куда бота добавили администратором: Telegram присылает об этом событие my_chat_member."""
    found = {}
    for u in tg(token, "getUpdates"):
        ev = u.get("my_chat_member") or {}
        chat = ev.get("chat") or {}
        if chat.get("type") == "channel":
            found[chat["id"]] = (chat.get("title") or "", (ev.get("new_chat_member") or {}).get("status"))
    return {cid: title for cid, (title, status) in found.items() if status == "administrator"}

def check_channel(token, chat_id):
    _, bot_id = check_bot(token)
    chat = tg(token, "getChat", chat_id=chat_id)
    member = tg(token, "getChatMember", chat_id=chat["id"], user_id=bot_id)
    if member.get("status") != "administrator":
        raise Fail(f"в канале «{chat.get('title')}» бот не администратор (статус: {member.get('status')})")
    if not member.get("can_post_messages"):
        raise Fail(f"в канале «{chat.get('title')}» у бота нет права «Публикация сообщений» — включи его")
    return chat["id"], chat.get("title") or ""

def step_channel(data):
    token = data["TELEGRAM_BOT_TOKEN"]
    bot = data.get("TELEGRAM_BOT_USERNAME", "бота")
    print("\n2/3 · Канал")
    if data.get("TELEGRAM_CHANNEL_ID"):
        try:
            cid, title = check_channel(token, data["TELEGRAM_CHANNEL_ID"])
            ans = input(f"  Уже записан: «{title}», бот там администратор. Enter — оставить, «н» — выбрать другой: ")
            if ans.strip().lower() not in ("н", "n"):
                print("  ✓ канал оставлен")
                return
        except Fail as e:
            print(f"  Записанный канал не прошёл проверку: {e}")
    print(f"  Создай канал (название — какое решишь, например «Полвека поэзии»; приватный тоже подходит).\n"
          f"  В канале: Настройки → Администраторы → Добавить → @{bot} → включено «Публикация сообщений» → Сохранить.")
    while True:
        ans = input("  Когда добавишь — нажми Enter (или введи @адрес публичного канала): ").strip()
        try:
            if ans:
                handle = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)?@?", "", ans).strip("/")
                cid, title = check_channel(token, "@" + handle)
            else:
                found = channels_from_updates(token)
                if not found:
                    print(f"  ✗ пока не вижу канала, где @{bot} администратор. Проверь, что добавлен именно этот бот"
                          " и именно администратором, и нажми Enter ещё раз.")
                    continue
                items = list(found.items())
                if len(items) > 1:
                    for i, (c, t) in enumerate(items, 1):
                        print(f"    {i}. {t}")
                    pick = input("  Какой канал Лютика? Номер: ").strip()
                    if not pick.isdigit() or not 1 <= int(pick) <= len(items):
                        continue
                    items = [items[int(pick) - 1]]
                cid, title = check_channel(token, items[0][0])
        except Fail as e:
            print(f"  ✗ {e}")
            continue
        data["TELEGRAM_CHANNEL_ID"], data["TELEGRAM_CHANNEL_TITLE"] = str(cid), title
        save(data)
        print(f"  ✓ канал «{title}»: бот администратор, публиковать может. Тестовых постов не делаю.")
        return


# ── шаг 3: GLM ────────────────────────────────────────────────────────────────
def rank_models(ids):
    """glm-x.y по убыванию версии, при равной версии — сначала без -air/-flash/-v."""
    def rank(m):
        v = re.match(r"glm-(\d+)(?:\.(\d+))?", m.lower())
        lite = re.search(r"air|flash|vision|\dv\b", m.lower())
        return (int(v.group(1)), int(v.group(2) or 0), 0 if lite else 1)
    return sorted((m for m in ids if re.match(r"glm-\d", m.lower())), key=rank, reverse=True)

def check_glm(key, base=GLM_BASE):
    auth = {"Authorization": f"Bearer {key}"}
    code, r = http("GET", f"{base}/models", headers=auth)
    if code in (401, 403):
        raise Fail(f"ключ не подошёл ({code}): {(r.get('error') or {}).get('message', r)}")
    ids = [m.get("id") for m in r.get("data", []) if m.get("id")] if code == 200 else []
    candidates = rank_models(ids) or GLM_FALLBACK          # список от API надёжнее моего
    last, no_package = None, []
    for model in candidates:
        code, r = http("POST", f"{base}/chat/completions", headers=auth, timeout=90, body={
            "model": model, "max_tokens": 64,
            "messages": [{"role": "user", "content": "Ответь одним словом: готово"}]})
        if code == 200:
            text = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return model, text.strip()[:60], ids
        e = r.get("error") or {}
        err = e.get("message") or r
        if code in (401, 403):
            raise Fail(f"ключ не подошёл ({code}): {err}")
        if str(e.get("code")) == "1113":                  # у этой модели нет пакета — пробую следующую
            no_package.append(model)
            continue
        if code == 429:
            raise Fail(f"ключ принят, но лимит ({code}): {err}. Запусти позже.")
        last = f"{model}: {code} {err}"
    if no_package and not last:
        raise Fail(f"ключ рабочий, но пакета под него нет: на все {len(no_package)} моделей Z.ai ответил 1113 "
                   "«нет баланса или пакета». Обычно это ключ из другого аккаунта или проекта, не того, где Coding Plan.")
    raise Fail(f"ни одна модель не ответила, последняя ошибка — {last}")

def step_glm(data):
    print("\n3/3 · Ключ GLM (Z.ai, Coding Plan)")
    if data.get("GLM_API_KEY"):
        print(f"  Уже записан: {mask(data['GLM_API_KEY'])}, модель {data.get('GLM_MODEL', '?')}.")
    print("  z.ai → личный кабинет → API Keys → создай ключ и скопируй.")
    while True:
        prompt = ("  Вставь ключ и нажми Enter" +
                  (", или просто Enter — оставить записанный" if data.get("GLM_API_KEY") else "") + ": ")
        key = getpass.getpass(prompt).strip() or data.get("GLM_API_KEY", "")
        if not key:
            continue
        print("  проверяю… (до минуты)")
        try:
            model, answer, ids = check_glm(key)
        except Fail as e:
            print(f"  ✗ {e}")
            if "лимит" in str(e):
                data["GLM_API_KEY"], data["GLM_BASE_URL"] = key, GLM_BASE
                save(data)
                return
            continue
        data.update(GLM_API_KEY=key, GLM_BASE_URL=GLM_BASE, GLM_MODEL=model)
        save(data)
        if ids:
            print(f"  модели по ключу: {', '.join(ids)}")
        said = f" и ответила «{answer}»" if answer else " и ответила"
        print(f"  ✓ ключ работает: модель {model}{said}. Ключ записан ({mask(key)})")
        return


def glm_from_clipboard():
    """Только ключ GLM: взять из буфера, проверить, записать. Не прошёл проверку — не записывает."""
    data = load()
    input("Скопируй ключ GLM (z.ai → API Keys) и нажми здесь Enter: ")
    key = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        print(f"✗ в буфере не ключ ({len(key)} знаков). Скопируй ключ и запусти команду ещё раз. Ничего не записал.")
        sys.exit(1)
    print(f"взял из буфера {mask(key)}, проверяю… (до минуты)")
    try:
        model, _, _ = check_glm(key)
    except Fail as e:
        print(f"✗ {e}\nНичего не записал.")
        sys.exit(1)
    data.update(GLM_API_KEY=key, GLM_BASE_URL=GLM_BASE, GLM_MODEL=model)
    save(data)
    print(f"✓ ключ работает, модель {model}. Записал в {SECRETS} ({mask(key)}). Можно возвращаться в чат.")


ANYMODEL_BASE = "https://anymodel.org/v1"

def anymodel_from_clipboard():
    """Ключ anymodel.org (OpenAI-совместимый шлюз): взять из буфера, спросить у него каталог моделей, записать.
    Печатает, что в каталоге есть со слухом, голосом и рисованием — по именам моделей."""
    import json, urllib.error, urllib.request
    data = load()
    input("Скопируй ключ anymodel.org и нажми здесь Enter: ")
    key = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        print(f"✗ в буфере не ключ ({len(key)} знаков). Скопируй ключ и запусти команду ещё раз. Ничего не записал.")
        sys.exit(1)
    print(f"взял из буфера {mask(key)}, спрашиваю каталог…")
    req = urllib.request.Request(ANYMODEL_BASE + "/models", headers={                 # без браузерного UA Cloudflare отвечает 403 (код 1010)
        "Authorization": f"Bearer {key}", "User-Agent": "Mozilla/5.0 (Macintosh) lyutik/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            ids = sorted(m.get("id", "") for m in json.loads(r.read().decode()).get("data", []))
    except urllib.error.HTTPError as e:
        print(f"✗ anymodel ответил {e.code}: {e.read().decode(errors='replace')[:200]}\nНичего не записал.")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"✗ нет связи с anymodel: {e.reason}\nНичего не записал.")
        sys.exit(1)
    data.update(ANYMODEL_API_KEY=key, ANYMODEL_BASE_URL=ANYMODEL_BASE)
    save(data)
    print(f"✓ ключ принят, моделей в каталоге: {len(ids)}. Записал в {SECRETS} ({mask(key)}).")
    kinds = {"слух / звук": ("audio", "omni", "realtime", "whisper", "transcribe", "voice"),
             "голос (TTS)": ("tts", "speech"), "рисование": ("image", "dall", "flux", "imagen", "cogview", "kandinsky"),
             "зрение (по имени не видно, у GPT/Claude/Gemini есть)": ("gpt-", "claude", "gemini")}
    for label, needles in kinds.items():
        hits = [i for i in ids if any(n in i.lower() for n in needles)]
        print(f"  {label}: {', '.join(hits[:12]) if hits else '—'}" + (" …" if len(hits) > 12 else ""))
    print("  весь каталог:", ", ".join(ids[:60]) + (" …" if len(ids) > 60 else ""))
    print("Можно возвращаться в чат.")


def eleven_from_clipboard():
    """Ключ ElevenLabs: взять из буфера, спросить у них, кто мы и сколько знаков осталось, записать."""
    import json, urllib.error, urllib.request
    data = load()
    input("Скопируй ключ ElevenLabs (Profile → API keys) и нажми здесь Enter: ")
    key = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        print(f"✗ в буфере не ключ ({len(key)} знаков). Ничего не записал.")
        sys.exit(1)
    req = urllib.request.Request("https://api.elevenlabs.io/v1/user/subscription", headers={
        "xi-api-key": key, "User-Agent": "Mozilla/5.0 (Macintosh) lyutik/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            sub = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print(f"✗ ElevenLabs ответил {e.code}: {e.read().decode(errors='replace')[:200]}\nНичего не записал.")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"✗ нет связи с ElevenLabs: {e.reason}\nНичего не записал.")
        sys.exit(1)
    data.update(ELEVEN_API_KEY=key)
    save(data)
    used, limit = sub.get("character_count", "?"), sub.get("character_limit", "?")
    print(f"✓ ключ принят: тариф {sub.get('tier', '?')}, знаков использовано {used} из {limit}. Записал ({mask(key)}). Можно возвращаться в чат.")


def openrouter_from_clipboard():
    """Ключ OpenRouter: взять из буфера, спросить остаток на счёте и какие модели ElevenLabs там есть, записать."""
    import json, urllib.error, urllib.request
    data = load()
    input("Скопируй ключ OpenRouter (openrouter.ai → Keys) и нажми здесь Enter: ")
    key = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        print(f"✗ в буфере не ключ ({len(key)} знаков). Ничего не записал.")
        sys.exit(1)
    H = {"Authorization": f"Bearer {key}", "User-Agent": "Mozilla/5.0 (Macintosh) lyutik/1.0", "Accept": "application/json"}
    try:
        with urllib.request.urlopen(urllib.request.Request("https://openrouter.ai/api/v1/credits", headers=H), timeout=60) as r:
            cred = json.loads(r.read().decode()).get("data", {})
        with urllib.request.urlopen(urllib.request.Request("https://openrouter.ai/api/v1/models", headers=H), timeout=60) as r:
            ids = sorted(m.get("id", "") for m in json.loads(r.read().decode()).get("data", []))
    except urllib.error.HTTPError as e:
        print(f"✗ OpenRouter ответил {e.code}: {e.read().decode(errors='replace')[:200]}\nНичего не записал.")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"✗ нет связи с OpenRouter: {e.reason}\nНичего не записал.")
        sys.exit(1)
    data.update(OPENROUTER_API_KEY=key, OPENROUTER_BASE_URL="https://openrouter.ai/api/v1")
    save(data)
    left = (cred.get("total_credits") or 0) - (cred.get("total_usage") or 0)
    print(f"✓ ключ принят: на счёте примерно ${left:.2f}. Записал ({mask(key)}).")
    voice = [i for i in ids if "eleven" in i.lower() or "tts" in i.lower() or "speech" in i.lower()]
    print("  голосовые модели в каталоге: " + (", ".join(voice) if voice else "— по именам не нашёл"))
    print("Можно возвращаться в чат.")


def main():
    if "--glm-clipboard" in sys.argv:
        return glm_from_clipboard()
    if "--anymodel-clipboard" in sys.argv:
        return anymodel_from_clipboard()
    if "--eleven-clipboard" in sys.argv:
        return eleven_from_clipboard()
    if "--openrouter-clipboard" in sys.argv:
        return openrouter_from_clipboard()
    print(f"Ключи Лютика → {SECRETS}\nВне репозитория, права 600. Ctrl+C — выйти, записанное сохранится.")
    data = load()
    try:
        step_bot(data)
        step_channel(data)
        step_glm(data)
    except KeyboardInterrupt:
        print("\n\nОстановлено. Что успело пройти проверку — записано, повторный запуск продолжит.")
        sys.exit(1)
    print(f"\nГотово. В {SECRETS}:")
    for k in KEYS:
        if data.get(k):
            print(f"  {k} = {mask(data[k]) if k in SECRET_KEYS else data[k]}")
    print("Можно закрыть вкладку и вернуться в чат.")


if __name__ == "__main__":
    main()
