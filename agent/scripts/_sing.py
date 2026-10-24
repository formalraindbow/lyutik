#!/usr/bin/env python3
"""Голос и лютня → WAV. Запускается не системным питоном, а тем, где есть vosk_tts и numpy (VOICE_PYTHON).

    _sing.py <выход.wav> <голос: dmitry | svetlana | 0–4> "<текст>" ["<мелодия>"] [bpm]
             [--key "соль минор"] [--bpm 86] [--repeats 2] [--bass]

Мелодия — ноты и аккорды через пробел. Нота — буква, # или b, октава 1–6: «A3 C4 E4:2 - G3+B3+D4:3»;
«:доли» — длительность в четвертях (нота — 1, аккорд — 4), «-» — пауза, «+» — несколько нот разом.
Аккорд — буква, # или b, вид (ничего — мажор; m, 7, m7, maj7, dim, sus2, sus4, aug) и необязательная октава
корня: «Am F C G», «Gm3:4 Eb3:4», «E7». Без октавы корень ложится между Eb3 и D4, так что Am — это A3 C4 E4.
Строка из одних нот — ноты, как прежде; слово манеры, «с басом» или однозначный аккорд переводят всю строку
в аккордовый режим: там «F3» — фа мажор от третьей октавы, а нота пишется с точкой: «.F3».
Манера для аккордов — словом в строке, действует до следующего слова: «перебор» (по умолчанию: бас и верхние
струны восьмыми, как в Am–F–C–G), «арпеджио» (волной через две октавы), «бой» (все струны разом, вниз и вверх);
«с басом» — под каждым аккордом корень октавой ниже на сильную долю. Последний аккорд строки тянется.
Без мелодии: с тональностью (--key «соль минор» | «C major» | «Gm») — стандартный ход в ней (минор i–VI–III–VII,
мажор I–V–vi–IV) по четыре доли на аккорд, --repeats раз (по умолчанию 2), тоника в конце; без тональности
лютня перебирает Am–F–C–G под голос, как прежде. Пустой текст — только лютня.
Лютня — струна Карплуса–Стронга из шума, то есть настоящий щипок, а не семпл.
"""
import math, os, re, subprocess, sys, wave
import numpy as np

SR = 22050
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
DEFAULT_BARS = [["A3", "C4", "E4", "C4", "E4", "C4"], ["F3", "A3", "C4", "A3", "C4", "A3"],
                ["C4", "E4", "G4", "E4", "G4", "E4"], ["G3", "B3", "D4", "B3", "D4", "B3"]]
SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
RU_NOTE = {"до": 0, "ре": 2, "ми": 4, "фа": 5, "соль": 7, "ля": 9, "си": 11}
RU_NAME = {False: ["до", "до-диез", "ре", "ре-диез", "ми", "фа", "фа-диез", "соль", "соль-диез", "ля", "ля-диез", "си"],
           True: ["до", "ре-бемоль", "ре", "ми-бемоль", "ми", "фа", "соль-бемоль", "соль", "ля-бемоль", "ля", "си-бемоль", "си"]}
CHORDS = {"": (0, 4, 7), "maj": (0, 4, 7), "m": (0, 3, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10), "m7": (0, 3, 7, 10),
          "maj7": (0, 4, 7, 11), "dim": (0, 3, 6), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "aug": (0, 4, 8)}
NOTE_RE = re.compile(r"[A-Ga-g][#b]?[0-6]")
CHORD_RE = re.compile(r"([A-Ga-g])([#b]?)(maj7|maj|min|m7|m|7|dim|sus2|sus4|aug)?([1-6])?")   # m7 раньше m, иначе 7 уйдёт в октаву
MANNER = {"перебор": "pick", "арпеджио": "arp", "бой": "strum", "pick": "pick", "arpeggio": "arp", "arp": "arp", "strum": "strum"}
MANNER_RU = {"pick": "перебор", "arp": "арпеджио", "strum": "бой"}
BASS_WORDS = {"с", "бас", "басом", "bass"}                       # «с басом» — где угодно в строке
LOOKALIKE = str.maketrans("АВСЕавсе", "ABCEabce")                # кириллица, похожая на латиницу: «Аm» → «Am»
FLAT_KEYS = {("major", 5), ("major", 10), ("major", 3), ("major", 8), ("major", 1), ("major", 6),
             ("minor", 2), ("minor", 7), ("minor", 0), ("minor", 5), ("minor", 10), ("minor", 3)}   # где пишут бемоли
PROGRESSION = {"minor": ((0, "m"), (8, ""), (3, ""), (10, "")),  # i–VI–III–VII: Am F C G
               "major": ((0, ""), (7, ""), (9, "m"), (5, ""))}   # I–V–vi–IV: C G Am F
PICK = {3: ([0, 1, 2, 1], [0, 1, 2, 1, 2, 1]), 4: ([0, 1, 2, 3], [0, 1, 2, 3, 2, 1])}   # перебор на 4 и на 6 восьмых
STRUM = (("down", 1.0), None, ("down", 0.8), ("up", 0.6), None, ("up", 0.6), ("down", 0.8), ("up", 0.6))   # D . D U . U D U
CHORD_BEATS = 4.0                                                # аккорд без «:долей» — такт
BASS_GAIN = 0.65                                                 # бас тише мелодии: по щипку −4 дБ
HOLD = 3.5                                                       # последний аккорд тянется не меньше стольких секунд


def freq(name):
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(\d)", name)
    if not m:
        raise ValueError(f"не нота: {name}")
    semi = NOTE[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)] + 12 * (int(m.group(3)) + 1)
    return 440.0 * 2 ** ((semi - 69) / 12)

def pluck(f, seconds, decay=0.996):
    """Струна: кольцо шума длиной в период, каждый оборот сглаживается — звук затухает, как щипок."""
    n = max(2, int(SR / f))
    total = int(SR * seconds)
    buf = np.random.uniform(-1, 1, n)
    out = np.empty(total + n)
    i = 0
    while i < total:
        out[i:i + n] = buf
        buf = decay * 0.5 * (buf + np.roll(buf, -1))
        i += n
    env = np.exp(-np.linspace(0, 6, total)) * 0.6 + 0.4          # чуть мягче, чем голое кольцо
    fade = min(total, int(SR * 0.03))
    if fade:
        env[-fade:] *= np.linspace(1, 0, fade)                   # струна глушится мягко — без ступеньки на обрыве
    return out[:total] * env

def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)

def chord(m):
    """Разобранный аккорд → подпись, ноты снизу вверх (MIDI и имена) и корень октавой ниже для баса."""
    letter, acc, kind, octave = m.group(1).upper(), m.group(2), m.group(3) or "", m.group(4)
    pc = (NOTE[letter] + {"#": 1, "b": -1, "": 0}[acc]) % 12
    root = 12 * (int(octave) + 1) + pc if octave else (48 + pc if 48 + pc >= 51 else 60 + pc)   # корень между Eb3 и D4
    spell = FLATS if acc == "b" or (not acc and kind in ("m", "min", "m7", "dim")) else SHARPS
    midi = [root + step for step in CHORDS[kind]]
    return {"label": letter + acc + kind + (octave or ""), "midi": midi,
            "notes": [spell[n % 12] + str(n // 12 - 1) for n in midi], "bass": root - 12}

def is_notes(name):
    return name == "-" or name.startswith(".") or all(NOTE_RE.fullmatch(p) for p in name.split("+"))

def parse(melody):
    """Строка → (манера или None, бас?, элементы). Элемент: (вид, данные, доли, манера); вид — rest, notes, chord.

    Строка из одних нот читается как прежде. Слово манеры, «с басом» или однозначный аккорд (Am, F, E7, Gm3) переводят
    строку в аккордовый режим: там «F3» — фа мажор от третьей октавы, а нота пишется с точкой: «.F3»."""
    manner, bass, raw = None, False, []
    for tok in melody.split():
        low = tok.lower()
        if low in MANNER:
            manner = MANNER[low]
            continue
        if low in BASS_WORDS:
            bass = bass or low != "с"
            continue
        raw.append((tok.translate(LOOKALIKE), manner))
    chordy = manner is not None or bass or any(not is_notes(tok.partition(":")[0]) for tok, _ in raw)
    items = []
    for tok, man in raw:
        name, _, dur = tok.partition(":")
        try:
            beats = float(dur) if dur else 0.0
        except ValueError:
            raise ValueError(f"не длительность: {tok}") from None
        if dur and beats <= 0:
            raise ValueError(f"не длительность: {tok}")
        if name == "-":
            items.append(("rest", None, beats or 1.0, man))
        elif is_notes(name) and (not chordy or "+" in name or name.startswith(".")):
            notes = name.lstrip(".").split("+")
            if not all(NOTE_RE.fullmatch(n) for n in notes):
                raise ValueError(f"не нота: {tok}")
            items.append(("notes", notes, beats or 1.0, man))
        else:
            m = CHORD_RE.fullmatch(name)
            if not m:
                raise ValueError(f"не нота и не аккорд: {tok}")
            items.append(("chord", chord(m), beats or CHORD_BEATS, man))
    return manner, bass, items

def strum(ch, start, ring, gain, direction="down", gap=0.012):
    """Все струны аккорда почти разом — снизу вверх или сверху вниз, с крошечной задержкой между струнами."""
    notes = ch["midi"] if direction == "down" else ch["midi"][::-1]
    g = gain / math.sqrt(len(notes))
    return [(start + k * gap, hz(n), ring, g) for k, n in enumerate(notes)]

def play(ch, manner, start, length, beat, last):
    """Один аккорд → щипки (начало с, Гц, кольцо с, громкость). Последний аккорд — медленный проход по струнам, тянется."""
    if last:
        return strum(ch, start, max(length * 1.6, HOLD), 1.0, gap=0.045)
    eighth = beat / 2
    n = max(1, int(length / eighth + 1e-6))
    if manner == "strum":
        ev = []
        for k in range(n):
            hit = STRUM[k % len(STRUM)]
            if hit:
                ev += strum(ch, start + k * eighth, beat * 1.6, hit[1], hit[0], 0.012 if hit[0] == "down" else 0.008)
        return ev
    if manner == "arp":
        tones = ch["midi"] + [t + 12 for t in ch["midi"]]
        path = list(range(len(tones))) + list(range(len(tones) - 2, 0, -1))   # вверх через две октавы и обратно
        return [(start + k * eighth, hz(tones[path[k % len(path)]]), eighth * 1.6, 1.0) for k in range(n)]
    four, six = PICK[len(ch["midi"])]
    pat = six if n % 6 == 0 else four
    return [(start + k * eighth, hz(ch["midi"][pat[k % len(pat)]]), eighth * 1.6, 1.0) for k in range(n)]

def score(melody, bpm, repeats=1, bass=False):
    """Строка → все щипки дорожки и её длина в секундах. Строки из одних нот раскладываются как прежде."""
    _, bass_word, items = parse(melody)
    bass, beat, ev, cursor = bass or bass_word, 60.0 / bpm, [], 0.0
    sounding = [i for i, it in enumerate(items) if it[0] != "rest"]
    hold = sounding[-1] if sounding and items[sounding[-1]][0] == "chord" else None   # последний аккорд тянется
    times = max(1, repeats)
    for r in range(times):
        for i, (kind, data, beats, manner) in enumerate(items):
            length = beats * beat
            if kind == "notes":
                ev += [(cursor, freq(n), length * 1.6, 1 / math.sqrt(len(data))) for n in data]   # нота звучит дольше доли
            elif kind == "chord":
                last = i == hold and r == times - 1
                ev += play(data, manner or "pick", cursor, length, beat, last)
                if bass:
                    ev.append((cursor, hz(data["bass"]), max(length * 1.6, HOLD) if last else length * 1.2, BASS_GAIN))
            cursor += length
    total = cursor + 2.0
    if any(it[0] == "chord" for it in items):
        total = max(total, max(s + d for s, _, d, _ in ev) + 0.3)
    return ev, total

def mixdown(events, total):
    out = np.zeros(int(SR * total))
    for start, f, ring, gain in events:
        snd = pluck(f, ring) * gain
        a = int(SR * start)
        out[a:a + len(snd)] += snd[:len(out) - a]
    return out

def render(melody, bpm, repeats=1, bass=False):
    """Дорожка лютни по строке мелодии."""
    return mixdown(*score(melody, bpm, repeats, bass))

def default_melody(seconds, bpm):
    bars, t, i = [], 0.0, 0
    while t < seconds:
        bars.append(" ".join(f"{n}:0.5" for n in DEFAULT_BARS[i % 4]))
        t += 3 * 60.0 / bpm
        i += 1
    return " ".join(bars)

def parse_key(text):
    """«соль минор», «C major», «Gm», «F#m», «си-бемоль мажор» → (лад, ступень 0–11, подпись по-русски)."""
    s = re.sub(r"[-_,/]+", " ", text.strip().lower()).replace("♯", "#").replace("♭", "b")
    m = re.fullmatch(r"(до|ре|ми|фа|соль|ля|си)\s*(#|b|диез|бемоль)?\s*(.*)", s, re.S)
    if m:
        pc = RU_NOTE[m.group(1)] + {"#": 1, "диез": 1, "b": -1, "бемоль": -1, None: 0}[m.group(2)]
    else:
        m = re.fullmatch(r"([a-g])([#b]?)\s*(.*)", s, re.S)
        if not m:
            raise ValueError(f"не понял тональность «{text}» — пиши «соль минор», «C major» или «Gm»")
        pc = NOTE[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    rest = m.group(3).split()
    first = rest[0] if rest else ""
    if first == "" or first.startswith(("мажор", "маж", "major", "maj", "dur")):
        mode = "major"
    elif first == "m" or first.startswith(("минор", "мин", "minor", "min", "moll")):
        mode = "minor"
    else:
        raise ValueError(f"не понял лад в «{text}» — минор или мажор")
    pc %= 12
    return mode, pc, f"{RU_NAME[(mode, pc) in FLAT_KEYS][pc]} {'минор' if mode == 'minor' else 'мажор'}"

def progression(mode, pc, repeats, manner="pick"):
    """Стандартный ход в тональности: разбег арпеджио по тонике, четыре доли на аккорд, repeats раз, тоника в конце."""
    spell = FLATS if (mode, pc) in FLAT_KEYS else SHARPS
    names = [spell[(pc + step) % 12] + kind for step, kind in PROGRESSION[mode]]
    bar = " ".join(f"{n}:4" for n in names)
    return f"арпеджио {names[0]}:2 {MANNER_RU[manner]} " + " ".join([bar] * repeats) + f" {names[0]}:4", names

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
                        raise ValueError(f"--{name} без значения")
                    val = argv[i]
                flags[name] = val
            else:
                raise ValueError(f"неизвестный флаг --{name}; есть --key, --bpm, --repeats, --bass")
        else:
            args.append(a)
        i += 1
    return args, flags

EDGE = {"dmitry": "ru-RU-DmitryNeural", "svetlana": "ru-RU-SvetlanaNeural"}
ELEVEN_VOICES = {"george", "sarah", "adam", "alice", "bella", "bill", "brian", "callum", "charlie", "chris", "daniel", "eric",
                 "harry", "jessica", "laura", "liam", "lily", "matilda", "river", "roger", "will"}
ELEVEN_MODEL = os.environ.get("ELEVEN_MODEL") or "elevenlabs/eleven-v3"

def speak_eleven(text, voice, path):
    """ElevenLabs через OpenRouter: ключ и адрес приходят в окружении от voice.py. Ответ — сырой mp3."""
    import json, urllib.request
    key, base = os.environ.get("OPENROUTER_API_KEY"), (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    if not key:
        raise RuntimeError("нет ключа OpenRouter")
    body = json.dumps({"model": ELEVEN_MODEL, "input": text, "voice": voice, "response_format": "mp3"}, ensure_ascii=False).encode()
    req = urllib.request.Request(base + "/audio/speech", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Macintosh) lyutik/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        mp3 = r.read()
    if len(mp3) < 1000:
        raise RuntimeError(f"ответ без звука: {mp3[:120]!r}")
    with open(path + ".mp3", "wb") as f:
        f.write(mp3)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path + ".mp3", "-ac", "1", "-ar", str(SR), path], check=True)
    return load(path)


def load(path):
    with wave.open(path) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0

def speak(text, speaker, path):
    """Голос: ElevenLabs через OpenRouter (george, adam, …), Edge (dmitry, svetlana) или местный vosk (0–4).
    Сетевой не вышел — откат на местный, чтобы голосовое всё-таки ушло."""
    if speaker in ELEVEN_VOICES:
        try:
            return speak_eleven(text, speaker, path)
        except Exception as e:
            print(f"ElevenLabs не вышел ({type(e).__name__}: {str(e)[:100]}), беру Edge", file=sys.stderr)
            speaker = "dmitry"
    if speaker in EDGE:
        try:
            import asyncio, edge_tts
            mp3 = path + ".mp3"
            asyncio.run(edge_tts.Communicate(text, EDGE[speaker], rate="-6%").save(mp3))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-ac", "1", "-ar", str(SR), path], check=True)
            return load(path)
        except Exception as e:
            print(f"сетевой голос не вышел ({type(e).__name__}), беру местный", file=sys.stderr)
            speaker = "3"
    from vosk_tts import Model, Synth
    Synth(Model(model_name="vosk-model-tts-ru-0.7-multi")).synth(text, path, speaker_id=int(speaker))
    return load(path)

HELP = ("Нота — буква A–G, # или b, октава: A3, C#4, Bb3; аккорд — буква и вид: Am, F, E7, F#m, Bb, Am3:2; "
        "длительность через двоеточие: E4:2; манера словом: перебор, арпеджио, бой; «с басом» — бас под аккордами")

def main():
    try:
        args, flags = flags_of(sys.argv[1:])
        out, speaker, text = args[0], args[1].strip().lower() or "dmitry", args[2].strip()
        melody = args[3].strip() if len(args) > 3 else ""
        bpm = float(flags.get("bpm") or (args[4] if len(args) > 4 else 96.0))
        repeats = int(flags.get("repeats") or 0)
        if not 30 <= bpm <= 300:
            raise ValueError(f"темп {bpm:g} — нужен от 30 до 300 ударов в минуту")
        if not 0 <= repeats <= 8:
            raise ValueError(f"повторов {repeats} — нужно от 1 до 8")
        manner, bass_word, items = parse(melody)
    except (IndexError, ValueError) as e:
        print(f"{e}. {HELP}", file=sys.stderr)
        sys.exit(2)
    try:
        key = parse_key(flags["key"]) if flags.get("key") else None
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    bass = bool(flags.get("bass")) or bass_word
    voice = speak(text, speaker, out) if text else np.zeros(0)
    intro = int(SR * 1.5) if text else 0
    need = len(voice) / SR + 1.5
    if items:                                                        # своя мелодия
        times = repeats or 1
        if text:                                                     # под голосом мелодия крутится, пока голос не кончится
            span = sum(it[2] for it in items) * 60.0 / bpm
            times = max(times, math.ceil(need / span))
        lute = render(melody, bpm, times, bass)
        n_chords = sum(it[0] == "chord" for it in items)
        played = (f"лютня: своя мелодия, аккордов {n_chords}, нот {sum(it[0] == 'notes' for it in items)}"
                  + (f", ×{times}" if times > 1 else "") + (f", {MANNER_RU[manner or 'pick']}" if n_chords else "")
                  + (", с басом" if bass and n_chords else "") + f", {bpm:g} уд/мин"
                  + (f"; тональность «{key[2]}» к своей мелодии не применяется" if key else ""))
    else:
        if key is None and (repeats or bass or manner):
            key = ("minor", 9, "ля минор")                           # просили новое без тональности: тот же Am–F–C–G, но ходом
        if key is None:                                              # как прежде
            melody = default_melody(need, bpm)
            lute = render(melody, bpm)
            played = f"лютня: перебор Am–F–C–G, {bpm:g} уд/мин"
        else:
            mode, pc, label = key
            times = repeats or 2
            if text:                                                 # ход должен накрыть голос
                times = max(times, math.ceil((need * bpm / 60 - 6) / 16))
            melody, names = progression(mode, pc, times, manner or "pick")
            lute = render(melody, bpm, 1, bass)
            played = (f"лютня: {label}, {bpm:g} уд/мин, {'–'.join(names)} ×{times}, {MANNER_RU[manner or 'pick']}"
                      + (", с басом" if bass else ""))
    length = max(len(lute), intro + len(voice) + int(SR * 1.0))
    mix = np.zeros(length)
    mix[:len(lute)] += lute * (0.9 if not text else 0.32)
    if text:
        mix[intro:intro + len(voice)] += voice
        mix[:intro] += lute[:intro] * 0.5                           # вступление лютни громче, пока голос молчит
    tail = int(SR * 0.8)
    mix[-tail:] *= np.linspace(1, 0, tail)
    peak = np.max(np.abs(mix)) or 1.0
    mix = np.int16(np.clip(mix / peak * 0.9, -1, 1) * 32767)
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(mix.tobytes())
    print(played)
    print(f"{len(mix) / SR:.1f}")


if __name__ == "__main__":
    main()
