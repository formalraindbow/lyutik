#!/usr/bin/env python3
"""Видео-сниппет песни: обложка живёт в такт (медленный наезд, толчок на каждом ударе, лёгкая тряска), снизу —
спетая сейчас строка, сверху — «ЛЮТИК · НАЗВАНИЕ». Запускается окружением слуха (EARS_PYTHON): numpy и Pillow.
Удары — всплески звука, найденные numpy (librosa съедала 400 МБ при пределе службы 500); зерна нет: шум в каждом кадре
x264 не сжимает — 25 секунд весили 73 МБ при пределе Telegram для бота 50.

    _video.py <песня.mp3> <обложка> <начало, с> <длина, с> <строки.json> <выход.mp4> "<название>"

строки.json — [{"line", "start", "end"}], время от начала окна (из _align.py); пустой список — видео без субтитров.
"""
import json, math, random, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, FPS, BASE = 1080, 25, 1296                      # кадр, кадров в секунду, запас картинки под наезд и тряску
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(size):
    for path in (FONT, "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()

def wrap(draw, text, fnt, width):
    lines, cur = [], ""
    for word in text.split():
        probe = f"{cur} {word}".strip()
        if draw.textlength(probe, font=fnt) <= width or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])

def caption_layer(text, size=54):
    """Строка песни готовым слоем RGBA на весь кадр: белая, с чёрной обводкой, в нижней трети."""
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fnt = font(size)
    rows = wrap(d, text, fnt, 920)
    step = int(size * 1.25)
    y = 880 - step * (len(rows) - 1)
    for row in rows:
        x = (W - d.textlength(row, font=fnt)) / 2
        d.text((x, y), row, font=fnt, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0, 230))
        y += step
    return layer

def label_layer(title):
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((48, 44), f"ЛЮТИК · {title.upper()}", font=font(30), fill=(255, 255, 255, 210),
           stroke_width=2, stroke_fill=(0, 0, 0, 160))
    return layer

def beats_of(song, start, length, sr=22050, hop=512, n=2048):
    """Удары: всплески спектрального потока, не чаще чем раз в 0,22 с и заметно выше среднего."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", song,
                          "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    y = np.frombuffer(raw, dtype=np.float32)
    if len(y) < n * 2:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(y, n)[::hop] * np.hanning(n).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.maximum(np.diff(np.log1p(mag), axis=0), 0).sum(axis=1)
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    gap, beats, last = int(0.22 * sr / hop), [], -10 ** 9
    for i in range(1, len(flux) - 1):
        if flux[i] > 0.9 and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1] and i - last >= gap:
            beats.append((i + 1) * hop / sr)
            last = i
    return beats

def main():
    song, cover, start, length, lines_json, out, title = sys.argv[1:8]
    start, length = float(start), float(length)
    subs = json.load(open(lines_json, encoding="utf-8")) if lines_json != "-" else []
    beats = beats_of(song, start, length)
    rnd = random.Random(7)
    kick = {b: (rnd.uniform(-1, 1), rnd.uniform(-1, 1)) for b in beats}

    img = Image.open(cover).convert("RGB")
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))
    base = img.resize((BASE, BASE), Image.LANCZOS)

    yy, xx = np.mgrid[0:W, 0:W]                       # виньетка и надпись — один готовый слой поверх каждого кадра
    r = np.sqrt((xx - W / 2) ** 2 + (yy - W / 2) ** 2) / (W / 2)
    shade = (255 * (1 - np.clip(1.08 - 0.42 * r ** 2, 0.45, 1.0))).astype(np.uint8)
    over = Image.fromarray(np.dstack([np.zeros((W, W, 3), np.uint8), shade]), "RGBA")
    over.alpha_composite(label_layer(title))
    for i, s in enumerate(subs):                       # строка видна до следующей, но не дольше чем на 2,5 с после конца
        nxt = subs[i + 1]["start"] - 0.05 if i + 1 < len(subs) else length
        s["show"], s["hide"] = max(0.0, s["start"] - 0.1), min(nxt, s["end"] + 2.5, length)
        s["layer"] = caption_layer(s["line"])

    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{W}",
                            "-r", str(FPS), "-i", "-", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", song,
                            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                            "-pix_fmt", "yuv420p", "-af", f"afade=t=in:st=0:d=0.5,afade=t=out:st={length - 1.5:.2f}:d=1.5",
                            "-c:a", "aac", "-b:a", "192k", "-t", f"{length:.2f}", "-movflags", "+faststart", out],
                           stdin=subprocess.PIPE)
    for f in range(int(length * FPS)):
        t = f / FPS
        pulse, dx, dy = 0.0, 0.0, 0.0
        for b in beats:
            if 0 <= t - b < 0.5:
                k = math.exp(-(t - b) / 0.11)
                pulse += 0.028 * k
                dx += 7 * k * kick[b][0]
                dy += 7 * k * kick[b][1]
        scale = 1.0 + 0.07 * t / length + pulse    # медленный наезд + толчок на долю
        w = 1188 / scale
        cx, cy = BASE / 2 + dx, BASE / 2 + dy
        frame = base.crop((cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2)).resize((W, W), Image.BILINEAR).convert("RGBA")
        frame.alpha_composite(over)
        for s in subs:
            if s["show"] <= t < s["hide"]:
                a = min(1.0, (t - s["show"]) / 0.15, (s["hide"] - t) / 0.2)
                layer = s["layer"] if a >= 1 else Image.fromarray(
                    (np.asarray(s["layer"]) * np.array([1, 1, 1, a], dtype=np.float32)).astype(np.uint8), "RGBA")
                frame.alpha_composite(layer)
        enc.stdin.write(frame.convert("RGB").tobytes())
    enc.stdin.close()
    if enc.wait():
        sys.exit("ffmpeg не собрал видео")
    print(json.dumps({"beats": len(beats), "lines": len(subs), "out": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
