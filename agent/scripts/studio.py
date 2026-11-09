#!/usr/bin/env python3
"""Студия: работа над песней, как у живого музыканта — послушал придирчиво, поправил, послушал снова.

    studio.py check <путь к песне>                                   придирчивое прослушивание по замыслу
    studio.py edit <путь> <с какой секунды> <по какую> "<текст участка>"   переделать кусок 10–30 с ($0.1)
    studio.py remix <путь> "<звук: жанр, инструменты>"               тот же текст, другой звук ($0.2)
    studio.py rejudge <путь> "<почему>"                              перепроверить по прошлому разбору Mureka, бесплатно

check слушает тремя способами и сверяет с замыслом песни (want — что должно быть, avoid — чего нельзя, из song;
хор, женский голос и завывания проверяются всегда — он поёт один). Судья — разбор самой Mureka (/v1/song/describe,
$0.1: инструменты, голоса с манерой, жанры, теги): замер 8.11 — точно и одинаково от прогона к прогону. Сверка с замыслом,
а не только чёрный список: при названных в замысле инструментах любой другой — лишний; при «спокойно» — без громкого
настроения; надрыв в голосе («Powerful», «Operatic») — в отчёт как манера: решает замысел. Калибровка 8.11 на 8 дублях
с метками на слух: все 6 забракованных или с бэками — «править» (в том числе три песни канала, спетые с «no backing
vocals, no choir» в стиле через NanoGPT и клон: хор, барабаны, бас), оба чистых дубля прямой Mureka — «годно», и ухо
Вовы согласно: «по задаче лютня и ничего лишнего — норм». Характер исполнения проверка не меряет: оба чистых дубля
«без характера» на его ухо. Слух Gemini (anymodel)
подсказывает, где именно звучит, и отвечает про то, чего Mureka не размечает; его одиночное «слышу» идёт в сомнительное:
на прицельных вопросах он поддакивает и от прогона к прогону отвечает по-разному (замер 8.11: на электропопе «лютня есть,
спокойно»). Замеры (_listen.py: темп, тональность, громкость) — для правки. Лютню от нейлоновой гитары не отличает
ни одно ухо — поэтому в замысле лучше «щипковая акустика», а не «лютня».
Вердикт и дефекты пишутся в state/songs.jsonl — release выпускает только проверенную песню с вердиктом «годно».
edit и remix делают новую версию рядом (…_правка1.mp3, …_ремикс1.mp3) со строками и таймкодами; песни, заказанные
через Mureka напрямую, правятся по их номеру, остальные загружаются файлом. Всё — в бюджет песни (SONG_BUDGET)
и в канал-мастерскую.
"""
import base64, difflib, json, re, subprocess, sys, tempfile, urllib.error, urllib.request
from pathlib import Path
from _common import DRY, append_jsonl, now, read_jsonl, secrets
import drafts, listen, mureka_api, song

DESCRIBE_COST, EDIT_COST, REMIX_COST = 0.1, 0.1, 0.2
EARS = Path(__file__).with_name("_listen.py")
# что уши называют по-разному: основы слов для замысла (по-русски), разбора Mureka (англ. метки) и ответа Gemini;
# ищутся с начала слова — иначе «хор» находится в «хорошо», а «oud» в «loud»
KINDS = {
    "барабаны": r"drum|барабан|ударн",
    "перкуссия": r"percussion|clang|chime|bell|found.object|перкусс|заслонк|бутыл|ложк|бубен|трещотк|колокол",
    "синтезатор": r"synth|electronic|синт|электрон",
    "гармонь": r"accordion|bayan|garmon|аккордеон|гармон(?!ич)|баян",
    "хор и бэк-вокал": r"backing vocal|choir|choral|vocal harmon|harmonies|бэк|хор(?!ош)|подпев",
    "женский голос": r"female|женск",
    "рэп": r"rap\b|hip.?hop|рэп",              # «куплет почти речитатив» у барда — не рэп (9.11: ложное «нужно»)
    "поп": r"pop\b|поп\b|попс|эстрад|dance",
    "шансон": r"chanson|шансон",
    "электрогитара": r"electric guitar|distort|электрогитар|перегруз",
    "завывания": r"melisma|vocal runs|belting|рулад|завыв|распев|мелизм",
    "щипковая акустика": r"lute|nylon|acoustic guitar|classical guitar|mandolin|oud\b|plucked|лютн|акустик|щипков|нейлон",
    "спокойно": r"calm|gentle|intimate|melanchol|contemplative|спокой|тих|интимн|меланхол",
    "бас": r"bass|бас(?!н)",
    "смычковые": r"violin|fiddle|cello|viola|strings|скрип|виолонч|смычк",
    "клавишные": r"piano|keys|organ|harpsichord|рояль|пианин|фортепиан|орган|клавесин|клавиш",
    "духовые": r"brass|trumpet|flute|horn|sax|clarinet|whistle|флейт|саксоф|кларнет|духов|свирел|рожок",
}
INSTRUMENT_KINDS = {"барабаны", "перкуссия", "синтезатор", "гармонь", "электрогитара", "щипковая акустика", "бас", "смычковые",
                    "клавишные", "духовые"}
VOICE = re.compile(r"(?i)vocal|voice|choir|singer")
# так Mureka называет надрыв в голосе. Это не брак, а манера: ухо Вовы 8.11 — у чистых дублей «Warm, Intimate» голос
# «без характера, монотонный добренький», второй куплет просил надрыва. В отчёт — как описание, решает замысел
STRAIN = re.compile(r"(?i)\b(powerful|operatic|soaring|belting|strained|theatrical|dramatic|anthemic)\b")
LOUD_MOOD = re.compile(r"(?i)\b(energetic|driving|anthemic|soaring|cathartic|epic|uplifting|danceable|aggressive|"
                       r"powerful|dramatic|build-?up|high-energy)")
ALWAYS_AVOID = ["хор и бэк-вокал", "женский голос", "завывания"]   # поёт один (ядро, п. 12) — проверяется всегда
NEGATED = re.compile(r"(?i)\b(?:no|without|absence of|lack of|free of|devoid of)\s+(?:[\w-]+\s*){1,4}")


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def has(kind, text):
    return bool(re.search(r"(?<!\w)(?:" + KINDS[kind] + ")", text or "", re.I))

def kinds_in(text):
    """Какие виды из словаря названы в тексте замысла."""
    return [k for k in KINDS if has(k, text)]

def heard_by_mureka(kind, d):
    """Слышит ли разбор Mureka: метки инструментов, жанров и теги, плюс описание без отрицаний («no drums» — не барабаны)."""
    tags = " · ".join(map(str, (d.get("instrument") or []) + (d.get("genres") or []) + (d.get("tags") or [])))
    return has(kind, tags) or has(kind, NEGATED.sub(" ", d.get("description") or ""))

def heard_by_gemini(kind, g_items):
    """Ответ Gemini на пункт: по точному имени, иначе по пункту, где вид назван своими словами. None — промолчал."""
    x = g_items.get(kind) or next((v for name, v in g_items.items() if has(kind, name)), None)
    return (None, "") if x is None else (bool(x.get("heard")), x.get("where") or "")

def describe(path):
    url = "data:audio/mp3;base64," + base64.b64encode(Path(path).read_bytes()).decode()
    return mureka_api._call("POST", "/v1/song/describe", {"url": url}, timeout=240)

def gemini(path, items, measured):
    """Прицельные вопросы по замыслу: про каждый пункт — слышно ли и где. Ответ — JSON."""
    s = secrets()
    key, base = s.get("ANYMODEL_API_KEY"), (s.get("ANYMODEL_BASE_URL") or "").rstrip("/")
    if not key or not base:
        return {}
    tmp = Path(tempfile.mkdtemp(prefix="lyutik-check-")) / "clip.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(listen.HEAR_SECONDS), "-i", str(path), "-ac", "1",
                    "-b:a", "48k", str(tmp)], capture_output=True, timeout=120)
    ask = ("Ты строгий звукорежиссёр. Послушай песню и ответь только JSON без пояснений: "
           '{"items": [{"item": "<пункт>", "heard": true|false, "where": "<время или пусто>"}], '
           '"instruments": ["..."], "genre": "...", "vocal": "мужской/женский, манера", "mood": "..."}. '
           "Пункты, про каждый скажи, слышно ли это в записи: " + "; ".join(items) +
           ". Отвечай только про то, что реально звучит. Замеры прибора: " + measured[:300])
    body = {"model": s.get("HEAR_MODEL") or listen.HEAR_MODEL, "max_tokens": 900, "messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"data": base64.b64encode(tmp.read_bytes()).decode(), "format": "mp3"}},
        {"type": "text", "text": ask}]}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Macintosh) lyutik/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            text = json.loads(r.read().decode())["choices"][0]["message"].get("content") or ""
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
        return {}

def measure(path):
    py = secrets().get("EARS_PYTHON") or sys.executable
    try:
        r = subprocess.run([py, str(EARS), str(path), "--no-words"], capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, OSError):
        return ""                                         # замеры — подсказка для правки, без них проверка идёт
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith(("Темп", "Громкость", "Разброс"))]
    return " ".join(lines)[:400]

NEG = re.compile(r"(?i)(?<!\w)(без|никаких|никакого|никакой|ни|нет|не|no|without|кроме|except)(?!\w)")
INSTEAD = re.compile(r"(?i)(?<!\w)(?:вместо|instead of)\s+([^—,;:]+)")

def split_intent(want, avoid):
    """Запрет действует до конца фразы: «Никаких барабанов, синтезаторов, хора» — всё в «нельзя»;
    «вместо барабанов — заслонка» — барабаны в «нельзя», заслонка в «нужно» (9.11: резал по запятым — хор и синт
    попадали в «нужно»)."""
    yes, no = [], [avoid] if avoid else []
    for sent in re.split(r"[.;!\n]+", want or ""):
        no += [m.group(1).strip() for m in INSTEAD.finditer(sent)]
        sent = INSTEAD.sub(" ", sent)
        m = NEG.search(sent)
        if m:
            no.append(sent[m.start():].strip())
            sent = sent[:m.start()]
        if sent.strip(" ,—-:"):
            yes.append(sent.strip(" ,—-:"))
    return ", ".join(yes), ", ".join(no)

MOODS = {"спокойно"}

def plan(want, avoid):
    """Что проверять: виды «нужно» в порядке замысла (первый инструмент — ведущий), виды «нельзя» (с теми, что всегда),
    и пункты замысла своими словами."""
    pos = lambda k: re.search(r"(?<!\w)(?:" + KINDS[k] + ")", want, re.I).start()
    w_kinds = sorted(kinds_in(want), key=pos)
    a_kinds = [k for k in dict.fromkeys(kinds_in(avoid) + ALWAYS_AVOID) if k not in w_kinds]
    free = [x.strip() for x in re.split(r"[,;]", want) if x.strip() and not kinds_in(x)][:4]
    return w_kinds, a_kinds, free

GENERIC = {"acoustic", "electric", "electronic", "soft", "warm", "lead", "main", "solo", "clean", "light", "deep", "low", "high"}

def named_in(entry, text):
    """Инструмент назван в описании звука своим словом («Hurdy-Gurdy» — «hurdy-gurdy drone»); «no bass» не в счёт."""
    words = [w for w in re.sub(r"\(.*?\)", " ", entry.lower()).split() if w not in GENERIC]
    stem = re.sub(r"(?<=[^s])s$", "", words[0]) if words else ""      # drums → drum; bass остаётся bass
    return bool(stem) and bool(re.search(r"(?<!\w)" + re.escape(stem), NEGATED.sub(" ", text or "").lower()))

def judge(d, g, w_kinds, a_kinds, free, sound=""):
    """Решает разбор Mureka; Gemini — где именно, и сомнения там, где Mureka молчит.
    Сверка с замыслом, а не только чёрный список: когда в замысле названы инструменты, любой другой — лишний;
    при «спокойно» — без громкого настроения; надрыв в голосе — в сомнительное, как манера, а не брак."""
    g_items = {str(x.get("item", "")).split(" (")[0].strip().lower(): x for x in g.get("items") or []}
    confirmed, missing, doubtful = [], [], []
    for k in a_kinds:
        mu, (ge, where) = heard_by_mureka(k, d), heard_by_gemini(k, g_items)
        if mu:
            confirmed.append(f"{k}{f' ({where})' if ge and where else ''}")
        elif ge:
            doubtful.append(f"{k} — слышит только Gemini{f' ({where})' if where else ''}")
    unheard = [k for k in w_kinds if not heard_by_mureka(k, d)]
    named = [k for k in w_kinds if k in INSTRUMENT_KINDS]
    lead = named[0] if named else None                    # без ведущего инструмента или настроения — брак;
    missing = [k for k in unheard if k == lead or k in MOODS]   # второстепенное Mureka может просто не подписать (9.11: заслонка)
    doubtful += [f"{k} — в замысле есть, Mureka не слышит" for k in unheard if k not in missing]
    played = [str(x) for x in d.get("instrument") or [] if not VOICE.search(str(x))]
    if named:                                             # замысел назвал инструменты — остальные лишние
        extra = [x for x in played if not any(has(k, x) for k in named) and not any(has(k, x) for k in a_kinds)
                 and not named_in(x, sound)]
        if extra:
            confirmed.append("лишнее: " + ", ".join(extra))
    strain = sorted({m.group(1).capitalize() for x in d.get("instrument") or [] if VOICE.search(str(x))
                     for m in STRAIN.finditer(str(x))})
    if strain:
        doubtful.append("надрыв в голосе: " + ", ".join(strain) + " — по замыслу ли")
    if "спокойно" in w_kinds:
        loud = [str(t) for t in d.get("tags") or [] if LOUD_MOOD.search(str(t))]
        if loud:
            confirmed.append("не спокойно: " + ", ".join(loud))
    for x in free:                                        # своими словами: спросить можно только Gemini
        hit = g_items.get(x.lower())
        if hit is not None and not hit.get("heard"):
            doubtful.append(f"«{x}» — Gemini не слышит")
    return confirmed, missing, doubtful

def check(path_arg):
    src = song.local(path_arg)
    e = song.song_entry(src)
    title = e.get("title") or src.stem
    want, avoid = split_intent((e.get("want") or "").strip(), (e.get("avoid") or "").strip())
    if not want and not avoid:
        want = e.get("sound") or ""                       # старые песни без замысла — сверяем с описанием звука
    if DRY:                                               # сухой прогон не платит за уши, но и ворота релиза не держит
        append_jsonl(song.SONGS, {"at": now().isoformat(timespec="seconds"), "title": title, "checked": song.rel(src),
                                  "verdict": "годно", "dry": True})
        print(f"[сухой прогон] «{title}» не слушал придирчиво — считаю годной")
        return
    problem = song.budget_block(title, DESCRIBE_COST)
    if problem:
        fail(problem)
    measured = measure(src)
    try:
        d = describe(src)
    except mureka_api.MurekaError as err:
        fail(f"разбор Mureka не ответил: {err}")
    w_kinds, a_kinds, free = plan(want, avoid)
    g = gemini(src, [f"{k} (нужно)" for k in w_kinds] + [f"{k} (нельзя)" for k in a_kinds] + free, measured)
    confirmed, missing, doubtful = judge(d, g, w_kinds, a_kinds, free, e.get("sound") or "")
    verdict = "годно" if not confirmed and not missing else "править"
    rec = {"at": now().isoformat(timespec="seconds"), "title": title, "checked": song.rel(src), "verdict": verdict,
           "confirmed": confirmed, "missing": missing, "doubtful": doubtful, "cost": DESCRIBE_COST,
           "mureka": {k: d.get(k) for k in ("instrument", "genres", "tags", "description")}, "gemini": g or None}
    append_jsonl(song.SONGS, rec)
    lines = [f"«{title}», {src.name} — {verdict.upper()} (${DESCRIBE_COST}, на песню ушло ${song.spent_on(title):.2f})"]
    if confirmed:
        lines.append("Нельзя, а звучит: " + "; ".join(confirmed) + ".")
    if missing:
        lines.append("Нужно, а не звучит: " + "; ".join(missing) + ".")
    if doubtful:
        lines.append("Сомнительно (Gemini поддакивает — проверь сам по месту): " + "; ".join(doubtful) + ".")
    lines.append(f"Mureka: {', '.join(d.get('instrument') or [])} · жанры {', '.join(d.get('genres') or [])} · "
                 f"{', '.join((d.get('tags') or [])[:6])}.")
    if g:
        lines.append(f"Gemini: {g.get('genre', '')}; голос — {g.get('vocal', '')}; настроение — {g.get('mood', '')}.")
    else:
        lines.append("Gemini не ответил — где именно, не скажу.")
    if measured:
        lines.append("Замеры: " + measured)
    if verdict == "править":
        lines.append("Как править: не тот звук целиком — song_remix; плохо одно место — song_edit (10–30 с); "
                     "не то всё — новый заказ song; кончился бюджет — выпусти лучшее или в черновики.")
    else:
        lines.append("Можно выпускать: release с этим путём.")
    print("\n".join(lines))
    drafts.post(f"<b>«{drafts.esc(title)}»</b> · {drafts.esc(src.stem.split('_')[-1])} — проверка: <b>{verdict}</b>\n"
                + drafts.esc("\n".join(lines[1:3] if verdict == "править" else lines[1:2])))

def rejudge(path_arg, why):
    """Новые правила судьи — по прошлому разбору Mureka той же записи, без новой оплаты."""
    src = song.local(path_arg)
    last = song.last_check(src)
    if not last.get("mureka"):
        fail("у записи нет прошлой проверки с разбором Mureka — нужна song_check")
    e = song.song_entry(src)
    want, avoid = split_intent((e.get("want") or "").strip(), (e.get("avoid") or "").strip())
    if not want and not avoid:
        want = e.get("sound") or ""
    w_kinds, a_kinds, free = plan(want, avoid)
    confirmed, missing, doubtful = judge(last["mureka"], last.get("gemini") or {}, w_kinds, a_kinds, free, e.get("sound") or "")
    verdict = "годно" if not confirmed and not missing else "править"
    append_jsonl(song.SONGS, {"at": now().isoformat(timespec="seconds"), "title": last.get("title"), "checked": song.rel(src),
                              "verdict": verdict, "confirmed": confirmed, "missing": missing, "doubtful": doubtful, "cost": 0,
                              "mureka": last["mureka"], "gemini": last.get("gemini"), "rejudged": why, "of_check": last.get("at")})
    print(f"{src.name}: было «{last.get('verdict')}», стало «{verdict}» — " + "; ".join(confirmed + [f"нет: {m}" for m in missing]))

def version_path(src, kind):
    n = 1
    while True:
        p = src.with_name(f"{src.stem}_{kind}{n}.mp3")
        if not p.exists():
            return p
        n += 1

def sung(lyrics):
    return [l.strip() for l in (lyrics or "").splitlines() if l.strip() and not re.match(r"^\s*\[.*\]\s*$", l)]

def edited_lyrics(src, e, start, end, text):
    """Текст после правки: строки оригинала, спетые в окне (серединой внутри), заменены новыми; разметка остаётся.
    Нет таймкодов оригинала — None: тогда текст восстанавливается по расшифровке Mureka (restored)."""
    lyr, tl = e.get("lyrics") or "", src.with_suffix(".lines.json")
    if not lyr or not tl.exists():
        return None
    norm = lambda x: re.sub(r"[^\w]+", " ", x.lower()).strip()
    inside = {norm(r["line"]) for r in json.loads(tl.read_text(encoding="utf-8"))
              if start <= (r["start"] + r["end"]) / 2 <= end}
    out, placed = [], False
    for line in lyr.splitlines():
        if line.strip() and norm(line) in inside:
            if not placed:
                out += text.splitlines()
                placed = True
            continue
        out.append(line)
    return "\n".join(out) if placed else None

def restored(heard, original, start, end, text):
    """Без таймкодов оригинала: строка расшифровки в окне — новый текст, вне окна — ближайшая строка оригинала
    (похожа меньше чем на 0.6 — как расслышала Mureka)."""
    sim = lambda a, b: difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    out, placed = [], False
    for h in heard:
        if start <= (h["start"] + h["end"]) / 2 <= end:
            if not placed:
                out += text.splitlines()
                placed = True
            continue
        best = max(original, key=lambda o: sim(o, h["line"]), default="")
        out.append(best if best and sim(best, h["line"]) >= 0.6 else h["line"])
    return "\n".join(out)

def keep_version(src, e, ch, kind, extra, cost, lyrics):
    url = ch.get("url") or ch.get("flac_url") or ch.get("wav_url")
    if not url:
        fail("Mureka вернула версию без звука")
    out = version_path(src, kind)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lyutik/1.0"}), timeout=180) as r:
        raw = r.read()
    tmp = out.with_suffix(".raw")
    tmp.write_bytes(raw)
    song.ffmpeg("-i", tmp, "-b:a", "192k", out)
    tmp.unlink()
    heard = mureka_api.lines_of(ch)          # у загруженного файла Mureka отдаёт свою расшифровку, а не наш текст
    meant = sung(lyrics)
    if heard and len(heard) == len(meant):   # таймкоды — её, слова — наши
        lines = [{**h, "line": m} for h, m in zip(heard, meant)]
        out.with_suffix(".lines.json").write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    rec = {"at": now().isoformat(timespec="seconds"), "title": e.get("title") or src.stem, "lyrics": lyrics,
           "heard_lyrics": "\n".join(h["line"] for h in heard) or None,
           "sound": e.get("sound"), "want": e.get("want"), "avoid": e.get("avoid"), "path": song.rel(out),
           "seconds": round(song.seconds_of(out)), "cost": cost, "mureka_id": ch.get("id"), "voice": e.get("voice"),
           **extra}
    if e.get("cover"):
        rec["cover"] = e["cover"]
    append_jsonl(song.SONGS, rec)
    return out

def source_id(e, src, purpose):
    if e.get("mureka_id"):
        return {"song_id": e["mureka_id"]}
    return {"upload_audio_id": mureka_api.upload(src, purpose)}

def edit(path_arg, start, end, text):
    src = song.local(path_arg)
    e = song.song_entry(src)
    title = e.get("title") or src.stem
    start, end, text = float(start), float(end), (text or "").strip()
    if not 10 <= end - start <= 30:
        fail("окно правки — от 10 до 30 секунд")
    if not text:
        fail("нужен текст участка — что спеть на этом месте")
    if DRY:
        print(f"[сухой прогон] «{title}»: правку {start:.0f}–{end:.0f} с не заказывал")
        return
    problem = song.budget_block(title, EDIT_COST)
    if problem:
        fail(problem)
    try:
        body = {"lyrics": text, "edit_start": int(start * 1000), "edit_end": int(end * 1000), **source_id(e, src, "audio")}
        ch = mureka_api.wait(mureka_api._call("POST", "/v1/song/region-edit", body)["id"], limit=420)[0]
    except (mureka_api.MurekaError, IndexError, KeyError) as err:
        fail(f"правка не вышла: {err}")
    lyrics = edited_lyrics(src, e, start, end, text) or restored(mureka_api.lines_of(ch), sung(e.get("lyrics")), start, end, text)
    out = keep_version(src, e, ch, "правка", {"edit_of": song.rel(src), "region": [start, end], "region_lyrics": text},
                       EDIT_COST, lyrics)
    print(f"правка «{title}» {start:.0f}–{end:.0f} с готова: {song.rel(out)} (${EDIT_COST}, на песню ушло "
          f"${song.spent_on(title):.2f}). Послушай придирчиво — song_check.")
    drafts.post(f"<b>«{drafts.esc(title)}»</b> — правка {start:.0f}–{end:.0f} с: «{drafts.esc(text[:120])}» · ${EDIT_COST}",
                out, title=f"{title} — правка")

def remix(path_arg, sound):
    src = song.local(path_arg)
    e = song.song_entry(src)
    title, sound = e.get("title") or src.stem, (sound or "").strip()
    if not sound:
        fail("нужен звук ремикса: жанр, инструменты, темп")
    if DRY:
        print(f"[сухой прогон] «{title}»: ремикс не заказывал")
        return
    problem = song.budget_block(title, REMIX_COST)
    if problem:
        fail(problem)
    prompt = f"{sound.rstrip(' .')}. {song.SOLO}"
    if len(prompt) > song.PROMPT_MAX:
        fail(f"звук {len(sound)} знаков — Mureka берёт до {song.PROMPT_MAX - len(song.SOLO) - 2}, сожми")
    try:
        body = {"prompt": prompt, "lyrics": e.get("lyrics") or "", "model": "mureka-9.5",
                **source_id(e, src, "remix")}
        try:
            task = mureka_api._call("POST", "/v1/song/remix", body)
        except mureka_api.MurekaError as err:           # 9.11: «The mureka-9.5 model is not supported for this user» —
            if "not supported for this user" not in str(err):   # ремикс на 9.5 закрыт нашему тарифу, на mureka-8 открыт
                raise
            task = mureka_api._call("POST", "/v1/song/remix", {**body, "model": "mureka-8"})
        ch = mureka_api.wait(task["id"], limit=420)[0]
    except (mureka_api.MurekaError, IndexError, KeyError) as err:
        fail(f"ремикс не вышел: {err}")
    out = keep_version(src, e, ch, "ремикс", {"remix_of": song.rel(src), "sound": sound}, REMIX_COST, e.get("lyrics") or "")
    print(f"ремикс «{title}» готов: {song.rel(out)} (${REMIX_COST}, на песню ушло ${song.spent_on(title):.2f}). "
          f"Послушай придирчиво — song_check.")
    drafts.post(f"<b>«{drafts.esc(title)}»</b> — ремикс: {drafts.esc(sound[:150])} · ${REMIX_COST}", out, title=f"{title} — ремикс")


def main():
    a = sys.argv[1:]
    if a[:1] == ["check"] and len(a) >= 2:
        check(a[1])
    elif a[:1] == ["edit"] and len(a) >= 5:
        edit(a[1], a[2], a[3], a[4])
    elif a[:1] == ["remix"] and len(a) >= 3:
        remix(a[1], a[2])
    elif a[:1] == ["rejudge"] and len(a) >= 3:
        rejudge(a[1], a[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
