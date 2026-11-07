#!/usr/bin/env python3
"""Видео-сниппет песни в принятом формате промо: вертикаль 9:16, фон — та же обложка крупно, размытая и притушенная,
по центру — обложка карточкой с тенью, над ней название, под ней — спетая сейчас строка. Первая секунда — глитч
по крупному плану обложки, на ней карточка влетает с наклоном и встаёт ровно; дальше кадр спокоен, живёт зерно.
Запускается окружением слуха (EARS_PYTHON): numpy и Pillow.

    _video.py <песня.mp3> <обложка> <начало, с> <длина, с> <строки.json> <выход.mp4> "<название>"

строки.json — [{"line", "start", "end"}], время от начала окна (из _align.py); «-» — без строк.
Так сделано по референсу (8.11, промо-сниппет 15 с): движение там только во вступлении — замер края карточки:
всплески до 1,2 с, дальше неподвижно; толчки на каждом ударе весь ролик читались самодельно.
"""
import json, math, random, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
CARD, CARD_Y = 900, 940                   # сторона карточки, центр по вертикали
GLITCH, DROP, SETTLE, TITLE_IN = 0.95, 0.9, 1.5, 1.8
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(size):
    for path in (BOLD, "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()

def text_layer(rows, cy, size, fill, squeeze=0.84, shadow=140, gap=1.18):
    """Строки текста по центру, сжатые по ширине (узкий гротеск из DejaVu Bold), с мягкой тенью."""
    fnt = font(size)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    widths = [probe.textlength(r, font=fnt) for r in rows]
    step = int(size * gap)
    tw, th = int(max(widths) + 40), step * len(rows) + 40
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, (r, w) in enumerate(zip(rows, widths)):
        d.text(((tw - w) / 2, 20 + i * step), r, font=fnt, fill=fill)
    layer = layer.resize((max(1, int(tw * squeeze)), th), Image.LANCZOS)
    a = layer.getchannel("A")
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: v * shadow // 255).filter(ImageFilter.GaussianBlur(7)))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x, y = (W - layer.width) // 2, int(cy - th / 2)
    out.alpha_composite(sh, (x, y + 5))
    out.alpha_composite(layer, (x, y))
    return out

def wrap(text, size, width, squeeze=0.84):
    fnt, probe = font(size), ImageDraw.Draw(Image.new("L", (1, 1)))
    rows, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if probe.textlength(cand, font=fnt) * squeeze <= width or not cur:
            cur = cand
        else:
            rows.append(cur)
            cur = word
    return rows + ([cur] if cur else [])

def title_layer(title):
    main, _, sub = title.partition(" (")
    size = 96
    while size > 60 and len(wrap(main.upper(), size, 980)) > 1:
        size -= 6
    rows = wrap(main.upper(), size, 980)
    layer = text_layer(rows, 345 - (len(rows) - 1) * size * 0.6, size, (232, 232, 232, 255))
    if sub:
        layer.alpha_composite(text_layer([sub.rstrip(")").lower()], 432, 44, (205, 205, 205, 235), squeeze=0.9))
    return layer

def line_layer(text):
    rows = wrap(text, 56, 940, squeeze=0.9)[:3]
    return text_layer(rows, 1535 + (len(rows) - 1) * 33, 56, (255, 255, 255, 255), squeeze=0.9, shadow=210)

def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3

def main():
    song, cover, start, length, lines_json, out, title = sys.argv[1:8]
    start, length = float(start), float(length)
    subs = json.load(open(lines_json, encoding="utf-8")) if lines_json != "-" else []
    rnd = random.Random(11)

    img = Image.open(cover).convert("RGB")
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))

    big = img.resize((2200, 2200), Image.LANCZOS)                   # фон: крупный план, размыт и притушен
    bg = big.crop(((2200 - W) // 2, (2200 - H) // 2, (2200 + W) // 2, (2200 + H) // 2)).filter(ImageFilter.GaussianBlur(30))
    bg = ImageEnhance.Brightness(ImageEnhance.Color(bg).enhance(0.8)).enhance(0.72).convert("RGBA")
    close = np.asarray(big.crop((700, 380, 700 + 800, 380 + 1422)).resize((W, H), Image.BILINEAR))   # крупный план для глитча

    card = img.resize((CARD, CARD), Image.LANCZOS).convert("RGBA")
    ImageDraw.Draw(card).rectangle((0, 0, CARD - 1, CARD - 1), outline=(255, 255, 255, 70), width=2)   # кромка: тёмная
    # обложка на тёмном фоне иначе сливается — у референса светлая обложка отделялась сама
    shadow = Image.new("RGBA", (CARD + 160, CARD + 160), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 165), (80, 80, 80 + CARD, 80 + CARD))
    shadow = shadow.filter(ImageFilter.GaussianBlur(30))

    def scene(scale=1.0, angle=0.0, alpha=1.0):
        frame = bg.copy()
        c = card if scale == 1 and angle == 0 else card.resize((int(CARD * scale),) * 2, Image.BILINEAR).rotate(
            angle, Image.BILINEAR, expand=True)
        s = shadow if scale == 1 and angle == 0 else shadow.resize((int(shadow.width * scale),) * 2, Image.BILINEAR).rotate(
            angle, Image.BILINEAR, expand=True)
        if alpha < 1:
            c = c.copy()
            c.putalpha(c.getchannel("A").point(lambda v: int(v * alpha)))
            s = s.copy()
            s.putalpha(s.getchannel("A").point(lambda v: int(v * alpha)))
        frame.alpha_composite(s, ((W - s.width) // 2, CARD_Y - s.height // 2 + 22))
        frame.alpha_composite(c, ((W - c.width) // 2, CARD_Y - c.height // 2))
        return frame

    still = scene()
    titled = still.copy()
    head = title_layer(title)
    titled.alpha_composite(head)
    for i, s in enumerate(subs):
        nxt = subs[i + 1]["start"] - 0.05 if i + 1 < len(subs) else length
        s["show"] = max(TITLE_IN, s["start"] - 0.1)
        s["hide"] = min(nxt, s["end"] + 1.2, length - 0.2)
        s["layer"] = line_layer(s["line"])

    grain = []
    for i in range(6):
        g = np.random.default_rng(i).normal(0, 9, (H // 2, W // 2)).astype(np.float32)
        g = np.asarray(Image.fromarray((g + 128).clip(0, 255).astype(np.uint8)).resize((W, H), Image.BILINEAR), np.int16) - 128
        grain.append(g[..., None])

    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", song,
                            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                            "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p",
                            "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={length - 1.2:.2f}:d=1.2",
                            "-c:a", "aac", "-b:a", "192k", "-t", f"{length:.2f}", "-movflags", "+faststart", out],
                           stdin=subprocess.PIPE)
    for f in range(int(length * FPS)):
        t = f / FPS
        if t < DROP:                                                  # глитч: крупный план, цвета врозь, рваные полосы
            k = 1 - t / DROP
            px = np.roll(close, (rnd.randint(-6, 6), rnd.randint(-6, 6)), axis=(0, 1)).copy()
            dx = int(6 + 26 * k * rnd.random())
            px[..., 0] = np.roll(px[..., 0], dx, axis=1)
            px[..., 2] = np.roll(px[..., 2], -dx, axis=1)
            for _ in range(rnd.randint(3, 8)):
                y0, h = rnd.randint(0, H - 140), rnd.randint(16, 140)
                px[y0:y0 + h] = np.roll(px[y0:y0 + h], rnd.randint(-110, 110), axis=1)
            noise = np.random.default_rng(f).normal(0, 26 * k + 8, (H // 4, W // 4, 3)).astype(np.float32)
            noise = np.asarray(Image.fromarray((noise + 128).clip(0, 255).astype(np.uint8)).resize((W, H), Image.NEAREST), np.int16) - 128
            frame = np.clip(px.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        else:
            if t < SETTLE:                                            # влёт карточки: с наклоном и крупнее — встаёт ровно
                e = ease_out((t - DROP) / (SETTLE - DROP))
                img_t = scene(1.18 - 0.18 * e, -7 * (1 - e), min(1.0, (t - DROP) / 0.12))
            elif t < TITLE_IN:
                img_t = still
            elif t < TITLE_IN + 0.3:
                img_t = still.copy()
                h2 = head.copy()
                h2.putalpha(head.getchannel("A").point(lambda v, a=(t - TITLE_IN) / 0.3: int(v * a)))
                img_t.alpha_composite(h2)
            else:
                img_t = titled
            live = [s for s in subs if s["show"] <= t < s["hide"]]
            if live:
                img_t = img_t.copy()
                for s in live:
                    a = min(1.0, (t - s["show"]) / 0.18, (s["hide"] - t) / 0.22)
                    lay = s["layer"]
                    if a < 1:
                        lay = lay.copy()
                        lay.putalpha(lay.getchannel("A").point(lambda v, a=a: int(v * a)))
                    img_t.alpha_composite(lay)
            frame = np.clip(np.asarray(img_t.convert("RGB"), np.int16) + grain[f % len(grain)], 0, 255).astype(np.uint8)
        enc.stdin.write(frame.tobytes())
    enc.stdin.close()
    if enc.wait():
        sys.exit("ffmpeg не собрал видео")
    print(json.dumps({"lines": len(subs), "out": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
