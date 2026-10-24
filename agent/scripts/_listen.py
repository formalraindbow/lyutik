#!/usr/bin/env python3
"""Слушать звук: измерить, разобрать по частям, расслышать слова. Запускается окружением ушей (EARS_PYTHON):
там librosa и faster-whisper, которых нет у системного питона.

    _listen.py <файл> [--no-words]

Печатает отчёт по-русски: длительность, темп, тональность, громкость и динамика по времени, части по повторам
и энергии, слова как спеты с таймкодами. Всё измерено, а не угадано; где уверенности мало — так и сказано.
"""
import math, os, subprocess, sys, tempfile, warnings
warnings.filterwarnings("ignore")
import numpy as np

SR = 22050
MAX_SECONDS = 600
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
RU_NOTES = {"C": "до", "C#": "до-диез", "D": "ре", "D#": "ре-диез", "E": "ми", "F": "фа", "F#": "фа-диез",
            "G": "соль", "G#": "соль-диез", "A": "ля", "A#": "ля-диез", "B": "си"}
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])   # профили Крумхансл
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
BARS = "▁▂▃▄▅▆▇█"


def mmss(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"

def to_wav(src):
    """Любой формат → wav 22 кГц моно, не длиннее MAX_SECONDS."""
    tmp = tempfile.mkdtemp(prefix="lyutik-ears-")
    out = os.path.join(tmp, "in.wav")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-t", str(MAX_SECONDS), "-ac", "1", "-ar", str(SR), out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"не прочитался звук: {r.stderr.strip()[-200:]}")
    return out

def key_of(chroma):
    prof = chroma.mean(axis=1)
    best = []
    for i in range(12):
        for mode, template in (("мажор", MAJOR), ("минор", MINOR)):
            r = np.corrcoef(prof, np.roll(template, i))[0, 1]
            best.append((r, NOTES[i], mode))
    best.sort(reverse=True)
    return best[0], best[1]

def sparkline(values, n=24):
    if len(values) == 0:
        return ""
    chunks = np.array_split(values, min(n, len(values)))
    means = np.array([c.mean() for c in chunks])
    lo, hi = means.min(), means.max()
    norm = (means - lo) / (hi - lo) if hi > lo else np.zeros_like(means)
    return "".join(BARS[min(7, int(v * 7.999))] for v in norm)

def segments_of(y, sr, duration):
    """Части по смене гармонии и тембра; похожие по гармонии части помечаются как повтор."""
    import librosa
    hop = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=hop)
    feats = np.vstack([chroma, mfcc / (np.abs(mfcc).max() or 1)])
    k = int(max(2, min(8, duration // 25)))
    bounds = librosa.segment.agglomerative(feats, k)
    times = list(librosa.frames_to_time(bounds, sr=sr, hop_length=hop)) + [duration]
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    parts = []
    for a, b in zip(times, times[1:]):
        if b - a < 3:
            continue
        sel = (rms_t >= a) & (rms_t < b)
        fsel = slice(int(a * sr / hop), max(int(a * sr / hop) + 1, int(b * sr / hop)))
        parts.append({"a": a, "b": b, "energy": float(rms[sel].mean()) if sel.any() else 0.0,
                      "chroma": chroma[:, fsel].mean(axis=1)})
    for i, p in enumerate(parts):                       # повторы: та же гармония, что у ранней части
        p["like"] = None
        for j in range(i):
            q = parts[j]
            denom = (np.linalg.norm(p["chroma"]) * np.linalg.norm(q["chroma"])) or 1
            if float(p["chroma"] @ q["chroma"] / denom) > 0.97 and q["like"] is None:
                p["like"] = j
                break
    return parts, rms

def whisper_model():
    """Модель Whisper: папка с файлами, если скачана вручную (WHISPER_DIR или ~/.local/share/lyutik/whisper-small),
    иначе имя «small» — тогда её скачает сам faster-whisper."""
    d = os.environ.get("WHISPER_DIR") or os.path.expanduser("~/.local/share/lyutik/whisper-small")
    return d if os.path.exists(os.path.join(d, "model.bin")) and os.path.exists(os.path.join(d, "config.json")) else "small"

def words_of(path):
    """Слова как спеты: Whisper (малая модель, int8 на процессоре). Вокала нет — скажет."""
    from faster_whisper import WhisperModel
    model = WhisperModel(whisper_model(), device="cpu", compute_type="int8")
    segs, info = model.transcribe(path, beam_size=1, vad_filter=False, condition_on_previous_text=False)
    lines, total = [], 0
    for s in segs:
        text = s.text.strip()
        if not text:
            continue
        lines.append((s.start, s.end, text))
        total += len(text)
        if total > 3000:
            break
    return lines, info.language, info.language_probability

def main():
    if len(sys.argv) < 2:
        sys.exit("использование: _listen.py <файл> [--no-words]")
    src, want_words = sys.argv[1], "--no-words" not in sys.argv
    import librosa
    wav = to_wav(src)
    y, sr = librosa.load(wav, sr=SR, mono=True)
    duration = len(y) / sr
    if duration < 2:
        sys.exit("звук короче двух секунд — слушать нечего")
    out = [f"Звук: {os.path.basename(src)} · {mmss(duration)}" + (f" (слушал первые {mmss(MAX_SECONDS)})" if duration >= MAX_SECONDS - 1 else "")]

    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    (r1, n1, m1), (r2, n2, m2) = key_of(chroma)
    sure = "уверенно" if r1 > 0.8 else "похоже" if r1 > 0.6 else "неуверенно"
    half = f" (или {tempo / 2:.0f}: у неспешных песен счётчик часто удваивает темп)" if tempo > 135 else ""
    out.append(f"Темп: {tempo:.0f} ударов в минуту{half}, долей насчитал {len(beats)}. Тональность: {RU_NOTES[n1]} {m1} ({sure}; "
               f"второй кандидат — {RU_NOTES[n2]} {m2}).")

    parts, rms = segments_of(y, sr, duration)
    db = 20 * np.log10(rms + 1e-6)
    loud_i, quiet_i = int(np.argmax(rms)), int(np.argmin(rms[rms > rms.max() * 0.02])) if (rms > rms.max() * 0.02).any() else 0
    hop_t = 512 / sr
    rng = float(np.percentile(db, 95) - np.percentile(db, 5))
    centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
    tone = "тёмный, низкий" if centroid < 1500 else "ровный" if centroid < 3000 else "яркий, звонкий"
    out.append(f"Громкость по времени: {sparkline(rms)} (слева начало, справа конец). Разброс громкости {rng:.0f} дБ — "
               + ("ровно, без вздохов" if rng < 8 else "живая динамика" if rng < 16 else "резкие перепады")
               + f"; самое громкое место — {mmss(loud_i * hop_t)}. Звук {tone}.")

    lines, lang, lang_p = ([], None, 0.0)
    if want_words:
        try:
            lines, lang, lang_p = words_of(wav)
        except Exception as e:
            out.append(f"Слова не разобрал: {type(e).__name__}: {str(e)[:120]}")
    vocal = [(a, b) for a, b, _ in lines]

    def has_voice(a, b):
        return any(min(b, vb) - max(a, va) > 1.0 for va, vb in vocal)
    if parts:
        emax = max(p["energy"] for p in parts) or 1
        out.append("Части:")
        for i, p in enumerate(parts):
            lvl = p["energy"] / emax
            kind = "тихо" if lvl < 0.4 else "средне" if lvl < 0.75 else "громко"
            voice = "с голосом" if has_voice(p["a"], p["b"]) else ("без голоса" if want_words and lines else "")
            like = f", похоже на часть {p['like'] + 1}" if p["like"] is not None else ""
            out.append(f"  {i + 1}. {mmss(p['a'])}–{mmss(p['b'])} — {kind}{', ' + voice if voice else ''}{like}")
    if want_words:
        if lines:
            lang_ru = {"ru": "русский", "en": "английский", "uk": "украинский", "de": "немецкий", "fr": "французский",
                       "es": "испанский", "it": "итальянский", "pl": "польский"}.get(lang, lang)
            out.append(f"Слова как расслышал (язык {lang_ru}, уверенность {lang_p:.0%}; Whisper путает слова в пении, "
                       f"цитировать с оглядкой):")
            for a, b, text in lines[:80]:
                out.append(f"  [{mmss(a)}] {text}")
        else:
            out.append("Слов не расслышал: либо нет вокала, либо он глубоко в миксе.")
    print("\n".join(out))


if __name__ == "__main__":
    main()
