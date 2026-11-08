#!/usr/bin/env python3
"""Сверка судьи студии с ухом: прогоняет studio.judge по разборам Mureka из studio_calibration.json
и печатает таблицу «дубль — ухо Вовы — проверка — за что». Бесплатно: разборы уже куплены и лежат в фикстуре.
Код выхода 1, если хоть один вердикт разошёлся с ухом, — так любое новое правило судьи сверяется до выкатки.

    python3 tools/studio_calibration.py
"""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent" / "scripts"))
os.environ.setdefault("LYUTIK_DRY", "1")
import studio  # noqa: E402

fx = json.loads((Path(__file__).with_name("studio_calibration.json")).read_text(encoding="utf-8"))
want, avoid = studio.split_intent(fx["intent"]["want"], fx["intent"]["avoid"])
w, a, free = studio.plan(want, avoid)
miss = 0
for t in fx["takes"]:
    c, m, doubt = studio.judge(t["mureka"], {}, w, a, free)
    verdict = "годно" if not c and not m else "править"
    miss += verdict != t["ear"]
    why = "; ".join(c + [f"нет: {x}" for x in m])
    src = "задача" if t.get("inferred") else "ухо"
    print(f"{'✓' if verdict == t['ear'] else '✗'} {t['take']:40} {src}: {t['ear']:7} проверка: {verdict:7} {why}")
direct = [t for t in fx["takes"] if not t.get("inferred")]
print(f"совпало {len(fx['takes']) - miss} из {len(fx['takes'])}; прямых меток на слух {len(direct)}, "
      f"остальные — по его задаче «лютня и ничего лишнего»")
sys.exit(1 if miss else 0)
