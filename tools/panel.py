#!/usr/bin/env python3
"""Панель Лютика: дневник по сути — что сказал, что опубликовал, что листал и запомнил, на что смотрел,
что сыграл, кого выгнал, когда проснётся.

    python3 tools/panel.py            http://127.0.0.1:8787
    python3 tools/panel.py --port 9000

Читает logs/events.jsonl (его пишет tools/run.py) и файлы персонажа. Ничего не меняет. Страница сама
обновляется каждые пару секунд. Картинки из agent/lyutik/media/ отдаёт по адресу /media/…, и только оттуда.
Адрес с #all на конце показывает всю историю, а не последние события.
"""
import argparse, datetime, json, mimetypes, os, re, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from compose_session import vitals   # приборы: те же строки, что персонаж видит о себе в промпте
except Exception:                         # панель живёт и без них
    vitals = None

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "agent" / "lyutik"
MEDIA = HOME / "media"
EVENTS = ROOT / "logs" / "events.jsonl"
SECRETS = Path.home() / ".config" / "lyutik" / "secrets.env"
FIRST_LOAD = 1500                     # сколько последних событий отдать при первом открытии: примерно сутки


def read(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except OSError:
        return ""

def jsonl(p):
    out = []
    for line in read(p).splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out

def when(s):
    """Время записи: со смещением, если оно записано; без него — местное."""
    try:
        t = datetime.datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo else t.astimezone()

def channel():
    title, link = "канал", ""
    for line in read(SECRETS).splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "TELEGRAM_CHANNEL_TITLE":
            title = v.strip().strip('"')
    for p in jsonl(HOME / "state" / "posts.jsonl"):
        m = re.match(r"(https://t\.me/[^/]+)/\d+$", p.get("link") or "")
        if m and "/c/" not in m.group(1):
            link = m.group(1)
    return {"title": title, "link": link}

def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False

def status(events):
    pid = next((e.get("pid") for e in reversed(events) if e["kind"] == "start"), None)
    last = next((e for e in reversed(events) if e["kind"] not in ("start", "vote", "note")), None)   # голос во сне и заметка не меняют, чем он занят
    if not pid or not alive(pid):
        return {"state": "stopped", "since": last["t"] if last else None}
    if not last:
        return {"state": "starting"}
    k = last["kind"]
    if k == "calling":
        return {"state": "thinking", "since": last["t"], "step": last.get("step")}
    if k == "sleeping":
        return {"state": "sleeping", "until": last.get("until"), "since": last["t"]}
    if k in ("stopped",):
        return {"state": "stopped", "since": last["t"]}
    if k == "error":
        return {"state": "error", "since": last["t"], "text": last.get("text")}
    return {"state": "acting", "since": last["t"]}

def banned(now):
    """Кто сейчас выгнан из обсуждения: по записям модерации, последнее действие по человеку решает."""
    out = {}
    for x in jsonl(HOME / "state" / "dialogs.jsonl"):
        if x.get("dry") or x.get("kind") != "moderation":
            continue
        uid = x.get("user_id")
        if x.get("action") == "ban":
            t, minutes = when(x.get("at")), x.get("minutes") or 0
            until = t + datetime.timedelta(minutes=minutes) if t and minutes else None
            out[uid] = {"name": x.get("name") or "?", "username": x.get("username") or "", "since": x.get("at"),
                        "until": until.isoformat(timespec="seconds") if until else None}
        elif x.get("action") == "unban":
            out.pop(uid, None)
    return [b for b in out.values() if not b["until"] or (when(b["until"]) or now) > now]

def polls():
    try:
        d = json.loads(read(HOME / "state" / "polls.json") or "{}")
    except ValueError:
        d = {}
    return [dict(v, id=k) for k, v in d.items() if isinstance(v, dict)][::-1]

def subscribers(events):
    """Сколько подписчиков — из последнего раза, когда он сам смотрел на канал снаружи (channel_info)."""
    for e in reversed(events):
        if e.get("kind") == "tool" and e.get("name") == "channel_info":
            m = re.search(r"подписчиков: (\d+)", str(e.get("result") or ""))
            if m:
                return {"n": int(m.group(1)), "t": e.get("t")}
    return None

def snapshot():
    events = jsonl(EVENTS)
    ents = []
    d = HOME / "memory" / "entities"
    for f in sorted(d.glob("*.md"), key=lambda x: -x.stat().st_mtime) if d.exists() else []:
        ents.append({"name": f.stem, "text": read(f), "mtime": int(f.stat().st_mtime)})
    persona_edits = [e["t"] for e in events if e["kind"] == "tool" and e.get("name") == "write_file"
                     and str((e.get("args") or {}).get("path", "")).endswith("PERSONA.md")]
    now = datetime.datetime.now()
    try:
        dials = list(vitals(HOME, now)) if vitals else []
    except Exception:
        dials = []
    return {
        "status": status(events),
        "goals": read(HOME / "GOALS.md"),
        "state": read(HOME / "memory" / "state.md"),
        "persona": read(HOME / "PERSONA.md"),
        "persona_edits": persona_edits,
        "entities": ents,
        "posts": [p for p in jsonl(HOME / "state" / "posts.jsonl") if not p.get("dry")][::-1],
        "tickets": jsonl(HOME / "state" / "tickets.jsonl")[::-1],
        "operator_pending": (HOME / "state" / "operator.txt").exists(),
        "channel": channel(),
        "sessions": sum(1 for e in events if e["kind"] == "session"),
        "vitals": dials,
        "subscribers": subscribers(events),
        "banned": banned(now.astimezone()),
        "polls": polls(),
    }

def events_after(after):
    lines = read(EVENTS).splitlines()
    start = max(0, len(lines) - FIRST_LOAD) if after < 0 else after
    out = []
    for i, line in enumerate(lines[start:], start):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        e["i"] = i
        out.append(e)
    return {"events": out, "next": len(lines)}


class Handler(BaseHTTPRequestHandler):
    def send(self, body, ctype, cache="no-store"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def media(self, rel):
        """Файл из agent/lyutik/media/ — и только оттуда: «..» и ссылки наружу за папку не выводят."""
        try:
            p = (MEDIA / unquote(rel)).resolve()
            if MEDIA.resolve() not in p.parents or not p.is_file():
                raise FileNotFoundError(rel)
            data = p.read_bytes()
        except (OSError, ValueError):
            self.send_response(404)
            self.end_headers()
            return
        self.send(data, mimetypes.guess_type(p.name)[0] or "application/octet-stream", cache="max-age=86400")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self.send(PAGE, "text/html; charset=utf-8")
        if u.path == "/api/events":
            after = int((parse_qs(u.query).get("after") or ["-1"])[0])
            return self.send(json.dumps(events_after(after), ensure_ascii=False), "application/json")
        if u.path == "/api/snapshot":
            return self.send(json.dumps(snapshot(), ensure_ascii=False), "application/json")
        if u.path.startswith("/media/"):
            return self.media(u.path[len("/media/"):])
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Лютик</title>
<style>
:root{
  --bg:#f6f4ef; --panel:#fffdf8; --ink:#1f1c17; --muted:#6f685c; --line:#e4ddd0;
  --accent:#8a3b12; --accent-soft:#f6e6d9; --think:#5b5f8f; --think-soft:#ecedf7;
  --warn:#a1620b; --warn-soft:#fbefd9; --bad:#a32626; --bad-soft:#f8e1e1;
  --sleep:#5d5874; --sleep-soft:#ebe9f2; --chip:#efe9dc;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#15130f; --panel:#1e1b16; --ink:#ece6da; --muted:#a39a8a; --line:#34302a;
    --accent:#e39a6a; --accent-soft:#3a2619; --think:#aab0e8; --think-soft:#23243a;
    --warn:#f0b45a; --warn-soft:#3a2c14; --bad:#f08a8a; --bad-soft:#3d1c1c;
    --sleep:#b9b3d6; --sleep-soft:#2a2736; --chip:#2b2721;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 20px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
h1{font:600 24px/1.2 Georgia,"Times New Roman",serif;margin:0}
.pill{display:inline-flex;align-items:center;gap:8px;padding:5px 12px;border-radius:999px;font-weight:600;font-size:14px}
.dot{width:9px;height:9px;border-radius:50%;background:currentColor}
.thinking{background:var(--think-soft);color:var(--think)} .thinking .dot,.acting .dot{animation:pulse 1.2s infinite}
.acting{background:var(--accent-soft);color:var(--accent)}
.sleeping{background:var(--sleep-soft);color:var(--sleep)}
.stopped,.error{background:var(--bad-soft);color:var(--bad)}
.starting{background:var(--warn-soft);color:var(--warn)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.meta{color:var(--muted);font-size:13px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}
.meta a{color:var(--accent)}
.toggle{margin-left:auto;display:flex;gap:6px;align-items:center;color:var(--muted);font-size:13px;cursor:pointer;user-select:none}
main{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:20px;padding:20px;max-width:1300px;margin:0 auto;align-items:start}
aside{position:sticky;top:76px;max-height:calc(100vh - 92px);overflow:auto;padding-right:4px}
@media (max-width:720px){main{grid-template-columns:1fr;padding:12px}header{padding:10px 12px}aside{position:static;max-height:none;overflow:visible}}
section.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
section.card h2{font:600 12px/1.2 -apple-system,sans-serif;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:0 0 8px}
.day{background:var(--panel);border:1px solid var(--line);border-radius:14px;margin-bottom:16px;overflow:hidden}
.day>summary{list-style:none;cursor:pointer;padding:12px 18px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.day>summary::-webkit-details-marker{display:none}
.day[open]>summary{border-bottom:1px solid var(--line)}
.when{font:600 17px/1.2 Georgia,serif} .why{color:var(--muted);font-size:13px}
.sum{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
.tag{background:var(--chip);border-radius:999px;padding:1px 9px;font-size:12.5px;color:var(--muted)}
.items{padding:12px 18px 16px}
.it{margin:12px 0;display:grid;grid-template-columns:24px minmax(0,1fr);gap:10px;align-items:start}
.it.quiet{margin:4px 0}
.ic{font-size:16px;line-height:24px;text-align:center}
.lbl{color:var(--muted);font-size:13px;margin-bottom:3px}
.said{font:16px/1.6 Georgia,"Times New Roman",serif;white-space:pre-wrap}
.thought{color:var(--think);background:var(--think-soft);border-radius:8px;padding:8px 12px;font-size:13.5px;white-space:pre-wrap}
.post{border:1px solid var(--accent);border-radius:12px;padding:12px 14px;white-space:pre-wrap;font:15.5px/1.6 Georgia,serif;background:var(--bg)}
a.lnk{color:var(--accent);font-weight:600;text-decoration:none} a.lnk:hover{text-decoration:underline}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:var(--chip);border-radius:8px;padding:2px 9px;font-size:13.5px}
details.chipd{display:inline-block} details.chipd>summary{list-style:none;cursor:pointer;background:var(--think-soft);color:var(--think);border-radius:8px;padding:2px 9px;font-size:13.5px}
details.chipd>summary::-webkit-details-marker{display:none}
details.chipd[open]{display:block;width:100%} details.chipd[open]>summary{display:inline-block}
.note{margin-top:6px;border-left:3px solid var(--think);padding:4px 12px}
.self{border:1px solid var(--think);border-radius:12px;padding:10px 14px}
.sleep{color:var(--sleep)}
.err{color:var(--bad)}
.live{color:var(--think);font-weight:600}
.dm{background:var(--warn-soft);border-radius:10px 10px 10px 2px;padding:8px 12px;white-space:pre-wrap}
.op{background:var(--warn-soft);border-left:3px solid var(--warn);padding:8px 12px;border-radius:6px;white-space:pre-wrap}
.gal{display:flex;flex-wrap:wrap;gap:8px;white-space:normal} .dm>.gal:not(:first-child){margin-top:8px}
.gal figure{margin:0;max-width:240px} .gal figcaption{font-size:12.5px;color:var(--muted);margin-top:2px}
a.ph{display:inline-block;line-height:0}
img.th{max-width:240px;max-height:240px;border-radius:8px;border:1px solid var(--line);background:var(--chip)}
img.ths{width:96px;height:96px;object-fit:cover;border-radius:8px;border:1px solid var(--line);background:var(--chip)}
.lk{display:grid;grid-template-columns:96px minmax(0,1fr);gap:10px;margin:6px 0;align-items:start}
.seen{font-size:14px;white-space:pre-wrap}
.mel{font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);white-space:pre-wrap;margin-top:4px}
.qt{color:var(--muted);font-size:12.5px;line-height:24px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.qt.full{white-space:pre-wrap;line-height:1.5;padding:3px 0}
.poll{border:1px solid var(--line);border-radius:10px;padding:8px 12px;background:var(--bg)}
.poll b{display:block;margin-bottom:4px} .poll .o{display:flex;gap:8px;font-size:14px} .poll .n{min-width:2.5ch;text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.vit{font-size:13.5px;margin:4px 0}
.md h2,.md h3,.md h4,.md h5{font:600 14.5px/1.3 -apple-system,sans-serif;margin:10px 0 4px}
.md p{margin:4px 0} .md ul{margin:4px 0;padding-left:18px}
.md .ref{color:var(--think)}
.ent{border-top:1px solid var(--line);padding:6px 0} .ent:first-child{border-top:0}
.ent>summary{cursor:pointer;font-weight:600}
.small{font-size:13px;color:var(--muted)}
.empty{color:var(--muted);font-style:italic}
.badge{display:inline-block;background:var(--think-soft);color:var(--think);border-radius:6px;padding:1px 8px;font-size:11.5px;font-weight:600;margin-left:6px;text-transform:none;letter-spacing:0}
</style>
</head>
<body>
<header>
  <h1>Лютик</h1>
  <span id="pill" class="pill starting"><span class="dot"></span><span id="pilltext">загружаю…</span></span>
  <div class="meta" id="meta"></div>
  <label class="toggle"><input type="checkbox" id="raw"> мысли модели</label>
</header>
<main>
  <div id="feed"><div class="empty">пока пусто</div></div>
  <aside>
    <section class="card"><h2>Приборы</h2><div id="vitals"></div></section>
    <section class="card" id="pollcard" hidden><h2>Опросы</h2><div id="polls"></div></section>
    <section class="card"><h2>Как он сейчас</h2><div id="state" class="md"></div></section>
    <section class="card"><h2>Посты <span id="npost"></span></h2><div id="posts"></div></section>
    <section class="card"><h2>Что он задумал</h2><div id="goals" class="md"></div></section>
    <section class="card"><h2>Память <span id="nent"></span></h2><div id="ents"></div></section>
    <section class="card"><h2>Кто он <span id="pedit"></span></h2><div id="persona" class="md"></div></section>
  </aside>
</main>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const dhm = t => t ? `${t.slice(8,10)}.${t.slice(5,7)} · ${t.slice(11,16)}` : "";
const hm = t => t ? t.slice(11,16) : "";
function dur(ms){ ms = Math.max(0, ms); const s = Math.floor(ms/1000), h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60;
  return h ? `${h} ч ${m} мин` : m ? `${m} мин ${x} с` : `${x} с`; }
const durMin = m => m >= 60 ? `${Math.floor(m/60)} ч${m % 60 ? ` ${m % 60} мин` : ""}` : `${m} мин`;
const plural = (n, a, b, c) => { const d = n % 10, h = n % 100; return d === 1 && h !== 11 ? a : d >= 2 && d <= 4 && (h < 12 || h > 14) ? b : c; };
const term = m => m === 1440 ? "сутки" : m === 2880 ? "двое суток" : m === 60 ? "час" : m === 10080 ? "неделю" : durMin(m);
let showRaw = false; try { showRaw = localStorage.getItem("raw") === "1"; } catch(e){}
function md(src){
  if (!src || !src.trim()) return '<div class="empty">пусто</div>';
  const out = []; let list = false;
  for (const raw of src.split("\n")){
    const line = esc(raw).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\[\[(.+?)\]\]/g, '<span class="ref">$1</span>')
      .replace(/(https?:\/\/[^\s<)]+)/g, '<a class="lnk" href="$1" target="_blank" rel="noopener">$1</a>').replace(/`([^`]+)`/g, "$1");
    const h = line.match(/^(#{1,4})\s+(.*)$/), li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li){ if (!list){ out.push("<ul>"); list = true; } out.push(`<li>${li[1]}</li>`); continue; }
    if (list){ out.push("</ul>"); list = false; }
    if (h) out.push(`<h${h[1].length + 1}>${h[2]}</h${h[1].length + 1}>`);
    else if (line.trim()) out.push(`<p>${line}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
}
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch(e){ return u; } };
const WHY = w => !w ? "" : w.startsWith("первое") ? "запустили" : w.startsWith("проснулся") ? "проснулся сам" : w.startsWith("разбудило задание") ? "разбудило твоё задание" : w.startsWith("разбудило сообщение") ? "разбудила личка" : w;

// фото от людей: цикл кладёт их в agent/lyutik/media/in/ и помечает в тексте «[фото: путь — посмотреть: look]»
const MEDIA = "agent/lyutik/media/";
const PHOTO = /\[фото: (.+?) — посмотреть: look\]/g;
function photos(text){                 // пути к картинкам из пометок; что осталось от текста — подпись
  const paths = [];
  const caption = String(text ?? "").replace(PHOTO, (_, p) => { paths.push(p.trim()); return ""; }).trim();
  return {paths, caption};
}
const mediaUrl = p => String(p || "").startsWith(MEDIA) ? "/media/" + p.slice(MEDIA.length).split("/").map(encodeURIComponent).join("/") : "";
function thumb(p, cls){                // миниатюра; по клику — картинка целиком в новой вкладке
  const u = mediaUrl(p);
  const name = esc(String(p || "").split("/").pop());
  return u ? `<a class="ph" href="${esc(u)}" target="_blank" rel="noopener"><img class="${cls || "th"}" src="${esc(u)}" loading="lazy" alt="${name}" onerror="this.closest('a').replaceWith(Object.assign(document.createElement('span'), {className: 'chip', textContent: this.alt}))"></a>`
           : `<span class="chip">${name}</span>`;   // файла уже нет — остаётся имя
}
const gallery = ph => ph && ph.length ? `<div class="gal">${ph.map(x => `<figure>${thumb(x.p)}${x.cap ? `<figcaption>${esc(x.cap)}</figcaption>` : ""}</figure>`).join("")}</div>` : "";
const pollBox = (q, opts) => `<div class="poll"><b>${esc(q)}</b>${(opts || []).map(o => `<div class="o">${o.votes == null ? "" : `<span class="n">${esc(o.votes)}</span>`}<span>${esc(o.text)}</span></div>`).join("")}</div>`;
const votes = n => `${n} ${plural(n, "голос", "голоса", "голосов")}`;

function keep(el, html){
  if (el._html === html) return;
  el._html = html;
  const opened = new Set(), closed = new Set();
  el.querySelectorAll("details[data-k]").forEach(d => (d.open ? opened : closed).add(d.dataset.k));
  el.innerHTML = html;
  el.querySelectorAll("details[data-k]").forEach(d => { if (opened.has(d.dataset.k)) d.open = true; else if (closed.has(d.dataset.k)) d.open = false; });
}
let next = location.hash === "#all" ? 0 : -1, days = [], snap = null, waiting = [];
function ingest(evs){
  for (const e of evs){
    if (["start", "sleeping", "stopped", "calling"].includes(e.kind)) continue;
    if (["dm", "comment", "vote"].includes(e.kind) && days.length && days[days.length - 1].end){ waiting.push(e); continue; }   // написали или проголосовали во сне — в следующую сессию
    if (e.kind === "session" || !days.length) days.push({head: e.kind === "session" ? e : {}, evs: waiting.splice(0), end: null});
    const d = days[days.length - 1];
    if (e.kind === "session") continue;
    if (e.kind === "session_end"){ d.end = e; continue; }
    d.evs.push(e);
  }
}
function digest(evs){
  const items = [];
  const add = (type, field, val) => {
    if (!val) return;
    let last = items[items.length - 1];
    if (!last || last.type !== type){ last = {type, q: [], read: [], names: [], pages: []}; items.push(last); }
    if (!last[field].includes(val)) last[field].push(val);
  };
  const last = () => items[items.length - 1];
  for (const e of evs){
    if (e.kind === "said") items.push({type: "said", text: e.text});
    else if (e.kind === "thought"){ if (showRaw) items.push({type: "thought", text: e.text}); }
    else if (e.kind === "error") items.push({type: "error", text: e.text});
    else if (e.kind === "note") items.push({type: "note", text: e.text});
    else if (e.kind === "vote"){ const v = {type: "vote", q: e.question, total: e.total || 0, options: e.options || []}, l = last();
      if (l && l.type === "vote" && l.q === v.q) items[items.length - 1] = v; else items.push(v); }    // голос за голосом — одна строка с последним счётом
    else if (e.kind === "dm"){ const ph = photos(e.text), l = last(), pics = ph.paths.map(p => ({p, cap: ph.caption}));
      if (pics.length && l && l.type === "dm" && l.name === e.name && l.photos.length) l.photos.push(...pics);   // пачка фото подряд — одна галерея
      else items.push({type: "dm", name: e.name, text: pics.length ? "" : ph.caption, photos: pics}); }
    else if (e.kind === "comment"){ const ph = photos(e.text);
      items.push({type: "comment", name: e.name, text: ph.paths.length ? "" : ph.caption, link: e.post_link, photos: ph.paths.map(p => ({p, cap: ph.caption}))}); }
    else if (e.kind === "tool"){
      const a = e.args || {}, r = String(e.result || "");
      const p = String(a.path || "");
      const ok = !/^(ошибка|не вышло|не спето|не опубликовано|не отправлено|отказ|не хватает|аргументы не разобрались)/.test(r);
      switch (e.name){
        case "canon_search": add("books", "q", a.query); break;
        case "canon_read": add("books", "read", r.split("\n")[0].split(" · абзацы")[0]); break;
        case "web_search": add("web", "q", a.query); break;
        case "web_read": add("web", "pages", a.url); break;
        case "remember": add("memory", "names", a.name); break;
        case "write_file":
          if (p.endsWith("PERSONA.md")){
            const last = items[items.length - 1];
            if (last && last.type === "persona") last.text = a.content; else items.push({type: "persona", text: a.content});
          } else if (p.includes("/memory/entities/")) add("memory", "names", p.split("/").pop().replace(/\.md$/, ""));
          else if (p.endsWith("GOALS.md")){ const last = items[items.length - 1]; if (!last || last.type !== "goals") items.push({type: "goals"}); }
          break;
        case "post": items.push({type: "post", text: a.text, res: r}); break;
        case "ticket": items.push({type: "ticket", text: a.text}); break;
        case "reply": items.push({type: "reply", text: a.text, ok: /^отправлено/.test(r), res: r, where: "в личку"}); break;
        case "reply_comment": items.push({type: "reply", text: a.text, ok: /^ответил/.test(r), res: r, where: "в комментариях"}); break;
        case "look": { const row = {p, q: a.question || "", text: r, ok}, l = last();
          if (l && l.type === "look") l.rows.push(row); else items.push({type: "look", rows: [row]}); break; }   // несколько картинок подряд — одним блоком
        case "lute": items.push({type: "lute", melody: a.melody || "", caption: a.caption || "", res: r, ok, t: e.t}); break;
        case "poll": items.push({type: "poll", q: a.question || "", options: String(a.options || "").split("|").map(s => s.trim()).filter(Boolean), res: r, ok}); break;
        case "ban": case "unban": case "delete_comment": case "delete_post": case "edit_post":
          items.push({type: "mod", name: e.name, a, res: r, ok}); break;
        case "describe_channel": items.push({type: "channel", what: "desc", text: a.text || "", res: r, ok}); break;
        case "channel_photo": items.push({type: "channel", what: "photo", path: p, res: r, ok}); break;
      }
    }
  }
  return items;
}
function postText(it){
  const link = (it.res.match(/https?:\/\/\S+/) || [""])[0];
  const full = snap && link ? (snap.posts.find(p => p.link === link) || {}).text : null;
  return {link, text: full || it.text || ""};
}
function entityNote(name){
  const e = snap && snap.entities.find(x => x.name === name);
  return e ? md(e.text) : '<div class="empty">заметка не найдена</div>';
}
function renderItem(it, key){
  const row = (ic, body) => `<div class="it"><div class="ic">${ic}</div><div>${body}</div></div>`;
  switch (it.type){
    case "said": return row("🗣", `<div class="said">${esc(it.text)}</div>`);
    case "thought": return showRaw ? row("💭", `<div class="thought">${esc(it.text)}</div>`) : "";
    case "note": return `<div class="it quiet"><div class="ic"></div><div class="qt${showRaw ? " full" : ""}" title="${esc(it.text)}">${esc(it.text)}</div></div>`;
    case "books": return row("📚", `<div class="lbl">листал книги</div><div class="chips">${it.q.map(q => `<span class="chip">${esc(q)}</span>`).join("")}${it.read.map(r => `<span class="chip">📖 ${esc(r)}</span>`).join("")}</div>`);
    case "web": return row("🌐", `<div class="lbl">смотрел в сети</div><div class="chips">${it.q.map(q => `<span class="chip">${esc(q)}</span>`).join("")}${it.pages.map(u => `<a class="chip lnk" href="${esc(u)}" target="_blank" rel="noopener">${esc(host(u))}</a>`).join("")}</div>`);
    case "memory": return row("🧠", `<div class="lbl">запомнил</div><div class="chips">${it.names.map((n, i) => `<details class="chipd" data-k="${key}m${i}"><summary>${esc(n)}</summary><div class="note md">${entityNote(n)}</div></details>`).join("")}</div>`);
    case "persona": return row("✍️", `<div class="self"><div class="lbl">переписал себя</div><details data-k="${key}p"><summary class="small">каким себя записал</summary><div class="md">${md(it.text)}</div></details></div>`);
    case "goals": return row("🎯", `<div class="small">поправил свои планы — справа, «Что он задумал»</div>`);
    case "post": { const p = postText(it);
      return row("📯", `<div class="lbl">${p.link ? `опубликовал · <a class="lnk" href="${esc(p.link)}" target="_blank" rel="noopener">открыть в канале</a>` : `<span class="err">пост не ушёл: ${esc(it.res)}</span>`}</div><div class="post">${esc(p.text)}</div>`); }
    case "dm": return row("✉️", `<div class="lbl">${esc(it.name)} пишет</div><div class="dm">${esc(it.text)}${gallery(it.photos)}</div>`);
    case "comment": return row("💬", `<div class="lbl">${esc(it.name)} комментирует${it.link ? ` <a class="lnk" href="${esc(it.link)}" target="_blank" rel="noopener">пост</a>` : ""}</div><div class="dm">${esc(it.text)}${gallery(it.photos)}</div>`);
    case "reply": return row("🗣", `<div class="lbl">${it.ok ? "ответил " + it.where : `<span class="err">ответ не ушёл: ${esc(it.res)}</span>`}</div><div class="said">${esc(it.text)}</div>`);
    case "ticket": return row("🎫", `<div class="lbl">просит разработчика</div><div>${esc(it.text)}</div>`);
    case "error": return row("⚠️", `<div class="err">${esc(it.text)}</div>`);
    case "look": { const n = it.rows.length;
      return row("👁", `<div class="lbl">посмотрел${n > 1 ? ` ${n} ${plural(n, "картинку", "картинки", "картинок")}` : ""}</div>${it.rows.map(x =>
        `<div class="lk">${thumb(x.p, "ths")}<div>${x.q ? `<div class="small">спросил: ${esc(x.q)}</div>` : ""}<div class="${x.ok ? "seen" : "err"}">${esc(x.text)}</div></div></div>`).join("")}`); }
    case "lute": {
      const m = it.res.match(/^спето (в канал|в личку|в ответ на №\d+)[^,]*, (\d+) с(?::\s*(https?:\/\/\S+))?/);
      const where = !m ? "" : m[1] === "в канал" ? "в канал" : m[1] === "в личку" ? "в личку" : `в комментарии, ${m[1]}`;
      const head = m ? `сыграл на лютне · голосовое ${where}, ${m[2]} с · ${hm(it.t)}${m[3] ? ` · <a class="lnk" href="${esc(m[3])}" target="_blank" rel="noopener">послушать</a>` : ""}`
                     : `<span class="err">лютня не зазвучала: ${esc(it.res)}</span>`;
      return row("🎵", `<div class="lbl">${head}</div>${it.caption ? `<div class="said">${esc(it.caption)}</div>` : ""}${it.melody ? `<div class="mel">${esc(it.melody)}</div>` : ""}`); }
    case "poll": {
      const link = (it.res.match(/https?:\/\/\S+/) || [""])[0];
      const live = snap && link ? (snap.polls || []).find(p => p.link === link) : null;
      const head = it.ok ? `спросил канал${link ? ` · <a class="lnk" href="${esc(link)}" target="_blank" rel="noopener">открыть опрос</a>` : ""}${live ? ` · ${votes(live.total || 0)}` : ""}` : `<span class="err">опрос не ушёл: ${esc(it.res)}</span>`;
      return row("📊", `<div class="lbl">${head}</div>${pollBox(it.q, live ? live.options : it.options.map(text => ({text})))}`); }
    case "vote": return row("🗳", `<div class="lbl">проголосовали · ${votes(it.total)}</div>${pollBox(it.q, it.options)}`);
    case "mod": {
      const a = it.a, r = it.res;
      if (!it.ok){ const what = {ban: "выгнать не вышло", unban: "вернуть не вышло", delete_comment: "удалить комментарий не вышло", delete_post: "снять пост не вышло", edit_post: "поправить пост не вышло"}[it.name];
        return row("⚠️", `<div class="err">${what}: ${esc(r)}</div>`); }
      if (it.name === "ban"){ const m = r.match(/^(.+?) выгнан из обсуждения (?:на (\d+) мин|навсегда)/), mins = m && m[2] ? +m[2] : 0;
        return row("⛔", `<div>выгнал из обсуждения ${mins ? "на " + term(mins) : "навсегда"} — <b>${esc(m ? m[1] : "автора комментария №" + a.comment)}</b><span class="small"> · за комментарий №${esc(a.comment)}</span></div>`); }
      if (it.name === "unban"){ const m = r.match(/^(.+?) снова может писать/);
        return row("↩", `<div>вернул в обсуждение — <b>${esc(m ? m[1] : "автора комментария №" + a.comment)}</b></div>`); }
      if (it.name === "delete_comment"){ const m = r.match(/^комментарий №(\d+)(?: \((.+?)\))? удалён/);
        return row("🗑", `<div>удалил комментарий №${esc(m ? m[1] : a.comment)}${m && m[2] ? ` <span class="small">(${esc(m[2])})</span>` : ""}</div>`); }
      if (it.name === "delete_post"){ const p = snap && snap.posts.find(x => x.message_id === +a.post), first = p ? (p.text || "").split("\n").find(x => x.trim()) || "" : "";
        return row("🗑", `<div>снял пост ${esc(a.post)} из канала${first ? `<span class="small"> · «${esc(first.slice(0, 90))}»</span>` : ""}</div>`); }
      if (it.name === "edit_post"){ const link = (r.match(/https?:\/\/\S+/) || [""])[0];
        return row("✏️", `<div class="lbl">поправил пост ${esc(a.post)}${link ? ` · <a class="lnk" href="${esc(link)}" target="_blank" rel="noopener">открыть в канале</a>` : ""}${/без разметки/.test(r) ? " · ушёл без разметки" : ""}</div><details data-k="${key}e"><summary class="small">новый текст</summary><div class="post" style="margin-top:6px">${esc(a.text)}</div></details>`); }
      return ""; }
    case "channel": return it.what === "desc"
      ? row("📝", it.ok ? `<div class="lbl">сменил описание канала</div><div>${esc(it.text)}</div>` : `<div class="err">описание не сменилось: ${esc(it.res)}</div>`)
      : row("🖼", it.ok ? `<div class="lbl">поставил аватар канала</div>${thumb(it.path, "ths")}` : `<div class="err">аватар не встал: ${esc(it.res)}</div>`);
  }
  return "";
}
function renderFeed(){
  if (!days.length){ keep($("#feed"), '<div class="empty">пока пусто</div>'); return; }
  const st = snap ? snap.status : {};
  keep($("#feed"), days.map((d, di) => [d, di]).reverse().map(([d, di], k) => {
    const items = digest(d.evs), latest = k === 0;
    const posts = items.filter(i => i.type === "post" && /https?:/.test(i.res)).length;
    const mem = items.filter(i => i.type === "memory").reduce((n, i) => n + i.names.length, 0);
    const dms = items.filter(i => i.type === "dm" || i.type === "comment").reduce((n, i) => n + Math.max(1, (i.photos || []).length), 0);
    const looks = items.filter(i => i.type === "look").reduce((n, i) => n + i.rows.length, 0);
    const lutes = items.filter(i => i.type === "lute" && i.ok).length, polls = items.filter(i => i.type === "poll" && i.ok).length;
    const bans = items.filter(i => i.type === "mod" && i.name === "ban" && i.ok).length;
    const tags = [dms && `✉️ ${dms}`, posts && `📯 ${posts === 1 ? "пост" : posts + (posts < 5 ? " поста" : " постов")}`, lutes && `🎵 ${lutes}`, polls && `📊 ${polls}`,
      looks && `👁 ${looks}`, bans && `⛔ ${bans}`, mem && `🧠 ${mem}`, items.some(i => i.type === "persona") && "✍️ переписал себя",
      d.end && `🌙 до ${hm(d.end.wake_at)}`].filter(Boolean).map(t => `<span class="tag">${t}</span>`).join("");
    const live = latest && st.state === "thinking" ? `<div class="it"><div class="ic">💭</div><div class="live" id="livethink">думает…</div></div>` : "";
    const end = d.end ? `<div class="it"><div class="ic">🌙</div><div class="sleep">уснул на ${durMin(d.end.sleep)} — до ${dhm(d.end.wake_at)}</div></div>` : "";
    const op = d.head.operator ? `<div class="it"><div class="ic">✉️</div><div class="op">ты попросил: ${esc(d.head.operator)}</div></div>` : "";
    return `<details class="day" data-k="d${di}"${latest ? " open" : ""}><summary><span class="when">${dhm(d.head.t)}</span><span class="why">${esc(WHY(d.head.wake))}</span><span class="sum">${tags}</span></summary>
      <div class="items">${op}${items.map((it, ii) => renderItem(it, `d${di}i${ii}`)).join("")}${live}${end}</div></details>`;
  }).join(""));
}
function renderSnap(){
  const s = snap;
  const vit = (s.vitals || []).map(v => `<div class="vit">${esc(v)}</div>`);
  if (s.banned && s.banned.length) vit.push(`<div class="vit">В бане: ${s.banned.map(b => `${esc(b.name)} — ${b.until ? "до " + dhm(b.until) : "навсегда"}`).join("; ")}.</div>`);
  keep($("#vitals"), vit.join("") || '<div class="empty">приборы молчат</div>');
  const pc = $("#pollcard"); pc.hidden = !(s.polls && s.polls.length);
  keep($("#polls"), (s.polls || []).map(p => `<div class="ent">${pollBox(p.question, p.options)}<div class="small">${dhm(p.at)} · ${p.closed ? "закрыт" : "идёт"} · ${votes(p.total || 0)}${p.link ? ` · <a class="lnk" href="${esc(p.link)}" target="_blank" rel="noopener">в канале</a>` : ""}</div></div>`).join(""));
  keep($("#state"), md(s.state.replace(/^# .*\n+/, "").replace(/^Переписывается.*\n+/m, "")));
  keep($("#goals"), md(s.goals.replace(/^# .*\n+/, "").replace(/^План, который.*\n+/m, "")));
  keep($("#persona"), md(s.persona.replace(/^# .*\n+/, "").replace(/^Это я\..*\n+/m, "")));
  $("#pedit").innerHTML = s.persona_edits.length ? `<span class="badge">правил себя · ${dhm(s.persona_edits.at(-1))}</span>` : "";
  $("#npost").innerHTML = s.posts.length ? `<span class="badge">${s.posts.length}</span>` : "";
  keep($("#posts"), s.posts.length ? s.posts.map(p => {
    const voice = p.format === "voice";
    const first = voice ? `🎵 голосовое${p.seconds ? `, ${p.seconds} с` : ""}` : (p.text || "").split("\n").find(x => x.trim()) || "пост";
    const when = `${dhm(p.at)}${p.edited_at ? ` · поправлен ${dhm(p.edited_at)}` : ""}${p.deleted_at ? ` · снят ${dhm(p.deleted_at)}` : ` · <a class="lnk" href="${esc(p.link)}" target="_blank" rel="noopener">${voice ? "послушать" : "в канале"}</a>`}`;
    return `<details class="ent" data-k="p${esc(p.link)}"><summary>${p.deleted_at ? "<s>" : ""}${esc(first.slice(0, 70))}${p.deleted_at ? "</s>" : ""}</summary><div class="small">${when}</div>${voice ? "" : `<div class="post" style="margin-top:6px">${esc(p.text)}</div>`}</details>`;
  }).join("") : '<div class="empty">постов нет</div>');
  $("#nent").innerHTML = s.entities.length ? `<span class="badge">${s.entities.length}</span>` : "";
  keep($("#ents"), s.entities.length ? s.entities.map(e => `<details class="ent" data-k="e${esc(e.name)}"><summary>${esc(e.name)}</summary><div class="md">${md(e.text.replace(/^# .*\n+/, ""))}</div></details>`).join("") : '<div class="empty">пока никого и ничего</div>');
  const ch = s.channel, sub = s.subscribers;
  $("#meta").innerHTML = `${ch.link ? `<a href="${esc(ch.link)}" target="_blank" rel="noopener">${esc(ch.title)}</a>` : esc(ch.title)}${sub ? `<span title="смотрел ${esc(dhm(sub.t))}">${sub.n} ${plural(sub.n, "подписчик", "подписчика", "подписчиков")}</span>` : ""}${s.operator_pending ? "<span>✉️ в ящике твоё задание</span>" : ""}`;
}
function tick(){
  if (!snap) return;
  const st = snap.status, now = Date.now();
  const since = st.since ? new Date(st.since).getTime() : now;
  let text = "запускается";
  if (st.state === "thinking") text = `думает · ${dur(now - since)}`;
  else if (st.state === "acting") text = "действует";
  else if (st.state === "sleeping") text = `спит до ${hm(st.until)} · ещё ${dur(new Date(st.until).getTime() - now)}`;
  else if (st.state === "stopped") text = "не запущен";
  else if (st.state === "error") text = "сбой";
  $("#pill").className = "pill " + st.state; $("#pilltext").textContent = text;
  const lt = $("#livethink"); if (lt) lt.textContent = `думает… ${dur(now - since)}`;
}
let busy = false;                     // один запрос за раз: иначе два ответа на after=-1 задвоят историю
async function poll(){
  if (busy) return;
  busy = true;
  try{ const r = await (await fetch(`/api/events?after=${next}`)).json(); next = r.next; if (r.events.length){ ingest(r.events); renderFeed(); } }catch(e){}
  finally{ busy = false; }
}
async function pollSnap(){
  try{ const was = snap && snap.status.state; snap = await (await fetch("/api/snapshot")).json(); renderSnap(); if (was !== snap.status.state || !was) renderFeed(); tick(); }catch(e){}
}
$("#raw").checked = showRaw;
$("#raw").addEventListener("change", e => { showRaw = e.target.checked; try { localStorage.setItem("raw", showRaw ? "1" : "0"); } catch(x){} renderFeed(); });
pollSnap().then(poll);
setInterval(poll, 2000); setInterval(pollSnap, 5000); setInterval(tick, 1000);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="панель Лютика")
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"панель Лютика: http://127.0.0.1:{a.port}  (Ctrl+C — закрыть)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
