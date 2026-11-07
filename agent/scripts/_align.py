#!/usr/bin/env python3
"""Тайминги строк песни в окне сниппета: Whisper слышит слова окна (текст песни — подсказкой), на них ложатся
строки настоящего текста. Показываются только строки, которые в окне правда спеты.

    _align.py <песня.mp3> <текст.txt> <начало, с> <длина, с> [модель]   → JSON [{"line", "start", "end"}], время от начала окна
"""
import difflib, json, os, re, subprocess, sys, tempfile
from faster_whisper import WhisperModel

def norm(w):
    return re.sub(r"[^а-яa-z]", "", w.lower().replace("ё", "е"))

def main():
    audio, text_path, start, length = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    # tiny на окне сниппета: 5 с и 290 МБ, строки ложатся как у small по всей песне (замер 8.11);
    # small на всей песне — 793 МБ при пределе службы 500, base — у самой границы
    name = sys.argv[5] if len(sys.argv) > 5 else os.environ.get("WHISPER_ALIGN_MODEL", "tiny")
    lines = [l.strip() for l in open(text_path, encoding="utf-8").read().splitlines()
             if l.strip() and not re.match(r"^\s*\[.*\]\s*$", l)]
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "clip.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", audio,
                        "-ac", "1", "-ar", "16000", clip], check=True)
        model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=1)
        segs, _ = model.transcribe(clip, language="ru", word_timestamps=True, beam_size=1,
                                   initial_prompt=" ".join(lines)[:700], condition_on_previous_text=False)
        words = [(w.word, w.start, w.end) for s in segs for w in (s.words or [])]
    heard = [norm(w)[:5] for w, _, _ in words]
    toks = [(norm(t)[:5], i) for i, l in enumerate(lines) for t in l.split() if norm(t)]
    sm = difflib.SequenceMatcher(None, [t for t, _ in toks], heard, autojunk=False)
    hits = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            hits.setdefault(toks[a + k][1], []).append(words[b + k][1:])
    out = []
    for i, l in enumerate(lines):
        n = len([t for t in l.split() if norm(t)])
        got = sorted(hits.get(i, []))
        if len(got) < max(2, 0.4 * n):
            continue                                   # строка в окне не спета — не показываем
        groups, cur = [], [got[0]]                     # совпадения далеко друг от друга — берём самую плотную кучку
        for g in got[1:]:
            if g[0] - cur[-1][1] > 3.0:
                groups.append(cur)
                cur = [g]
            else:
                cur.append(g)
        groups.append(cur)
        best = max(groups, key=len)
        out.append({"line": l, "start": round(best[0][0], 2), "end": round(best[-1][1], 2)})
    print(json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    main()
