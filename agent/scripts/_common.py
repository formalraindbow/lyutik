"""Общее для скриптов: где персонаж, где ключи, сухой ли прогон."""
import datetime, json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = os.environ.get("AGENT_NAME", "lyutik")
HOME = ROOT / "agent" / NAME
STATE = HOME / "state"
SECRETS = Path.home() / ".config" / "lyutik" / "secrets.env"
DRY = os.environ.get("LYUTIK_DRY") == "1"          # сухой прогон: в мир ничего не уходит


def now():
    return datetime.datetime.now().astimezone()

def secrets():
    data = {}
    if SECRETS.exists():
        for line in SECRETS.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"')
    return data

def tg_link(msg):
    """Ссылка на сообщение Telegram по ответу API: публичный канал — по имени, иначе — по номеру чата."""
    username = msg["chat"].get("username")
    cid = str(msg["chat"]["id"])
    return (f"https://t.me/{username}/{msg['message_id']}" if username
            else f"https://t.me/c/{cid[4:] if cid.startswith('-100') else cid.lstrip('-')}/{msg['message_id']}")

def append_jsonl(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
