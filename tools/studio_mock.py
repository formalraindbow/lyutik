#!/usr/bin/env python3
"""Мок-прогон студии без сети и денег: проверка → ворота релиза → правка → ремикс (с откатом на mureka-8) →
бюджет → кусок → перепроверка по прошлому разбору. Mureka, Gemini, замеры и мастерская подменены; всё пишется
в agent/lyutik_test/ и стирается в конце. Код выхода 1 — что-то разошлось с ожидаемым.

    python3 tools/studio_mock.py
"""
import contextlib, io, os, shutil, subprocess, sys
from pathlib import Path

os.environ["AGENT_NAME"] = "lyutik_test"
os.environ["LYUTIK_DRY"] = "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent" / "scripts"))
import _common, drafts, mureka_api, song, studio  # noqa: E402

HOME = _common.HOME
assert HOME.name == "lyutik_test", HOME
bad = []

def expect(what, ok):
    print(("✓ " if ok else "✗ ") + what)
    if not ok:
        bad.append(what)

def run(f, *a):
    out, code = io.StringIO(), 0
    with contextlib.redirect_stdout(out):
        try:
            f(*a)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue().strip()

def tone(path, sec=40, f=330):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={f}:duration={sec}",
                    "-b:a", "128k", str(path)], check=True)

try:
    songs_dir = HOME / "media" / "songs"
    songs_dir.mkdir(parents=True, exist_ok=True)
    (HOME / "state").mkdir(parents=True, exist_ok=True)
    src, alt = songs_dir / "2026-11-08_2000_тест_дубль1.mp3", songs_dir / "mureka_out.mp3"
    tone(src)
    tone(alt, 41, 440)
    _common.append_jsonl(song.SONGS, {"at": "2026-11-08T20:00:00", "title": "Тест", "sound": "lute ballad, hurdy-gurdy drone, NO bass",
        "lyrics": "[Verse]\nстрока раз\nстрока два", "path": song.rel(src), "seconds": 40, "cost": 0.15,
        "want": "Лютня ведёт, вместо барабанов — заслонка, темп спокойный. Никаких синтезаторов, хора, второго голоса.",
        "avoid": "", "mureka_id": "song-111"})
    posted, calls = [], []
    drafts.post = lambda text, audio=None, title="", performer="": posted.append(text) or ""
    D = {"instrument": ["Lead Vocal (Male, Warm)", "Nylon-String Acoustic Guitar", "Drum Kit", "Hurdy-Gurdy"],
         "genres": ["Folk"], "tags": ["melancholic", "calm"], "description": "A gentle folk song with no synthesizers."}
    def fake_call(method, path, body=None, form=None, timeout=120):
        calls.append((path, dict(body or {})))
        if path == "/v1/song/describe":
            return D
        if path == "/v1/song/remix" and (body or {}).get("model") == "mureka-9.5":
            raise mureka_api.MurekaError('Mureka ответила 400: {"error":{"message":"Invalid Request, The mureka-9.5 model is not supported for this user."}}')
        if path in ("/v1/song/region-edit", "/v1/song/remix"):
            return {"id": "task-9"}
        raise AssertionError(path)
    mureka_api._call = fake_call
    mureka_api.wait = lambda task_id, limit=480, step=6: [{"id": "song-222", "url": alt.as_uri(), "lyrics_sections": [
        {"lines": [{"text": "строка раз", "start": 1000, "end": 3000}, {"text": "строка новая", "start": 21000, "end": 24000}]}]}]
    G = {"items": [{"item": "барабаны", "heard": True, "where": "0:40"}], "genre": "фолк", "vocal": "мужской", "mood": "грусть"}
    studio.gemini = lambda path, items, measured: G
    studio.measure = lambda path: "Темп: 92 удара в минуту"

    expect("до проверки ворота закрыты", "сначала слушают" in song.check_block(src, "Тест"))
    code, out = run(studio.check, song.rel(src))
    rec = [x for x in _common.read_jsonl(song.SONGS) if x.get("checked")][-1]
    expect("барабаны подтверждены, шарманка из описания звука — не лишняя",
           rec["verdict"] == "править" and any("барабаны" in c for c in rec["confirmed"])
           and not any("Hurdy" in c for c in rec["confirmed"]))
    expect("синтезатор и хор в «нельзя», а не «нужно»", not rec["missing"])
    expect("после «править» ворота закрыты", "сказала «править»" in song.check_block(src, "Тест"))
    expect("вопреки ушам при живом бюджете — нельзя", "пока есть — правь" in song.check_block(src, "Тест", "так слышу"))
    code, out = run(studio.edit, song.rel(src), "20", "35", "строка новая")
    e2 = _common.read_jsonl(song.SONGS)[-1]
    expect("правка легла рядом со строками и ценой", code == 0 and e2.get("edit_of") == song.rel(src) and e2.get("cost") == 0.1)
    expect("окно короче 10 с — отказ", run(studio.edit, song.rel(src), "20", "25", "x")[0] == 1)
    D["instrument"] = ["Lead Vocal (Male, Warm)", "Nylon-String Acoustic Guitar"]
    G["items"][0]["heard"] = False
    code, out = run(studio.check, e2["path"])
    expect("проверка правки — годно, ворота открыты", "ГОДНО" in out and song.check_block(ROOT / e2["path"], "Тест") == "")
    code, out = run(studio.remix, song.rel(src), "baroque lute ballad, slow")
    expect("ремикс: 9.5 закрыт — откат на mureka-8", code == 0 and calls[-1][1].get("model") == "mureka-8")
    _common.append_jsonl(song.SONGS, {"at": "2026-11-08T21:00:00", "title": "Тест", "cost": 0.8, "path": "x"})
    expect("бюджет кончился — заказы и правки стоп", "бюджет песни кончился" in song.budget_block("Тест", 0.1))
    expect("вопреки ушам без бюджета — можно", song.check_block(src, "Тест", "лучшее из сделанного") == "")
    code, out = run(song.snippet, e2["path"], 12, 5)
    sn = [x for x in _common.read_jsonl(song.SONGS) if x.get("snippet")][-1]
    expect("кусок проверенной правки проходит ворота", song.check_block(ROOT / sn["snippet"], "Тест") == "")
    code, out = run(studio.rejudge, song.rel(src), "мок")
    expect("перепроверка по прошлому разбору — бесплатно", code == 0 and _common.read_jsonl(song.SONGS)[-1].get("cost") == 0)
    expect("мастерская получила проверки и правку", len(posted) >= 3)
finally:
    shutil.rmtree(HOME, ignore_errors=True)
print("мок чистый" if not bad else f"разошлось: {len(bad)}")
sys.exit(1 if bad else 0)
