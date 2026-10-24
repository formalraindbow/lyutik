#!/usr/bin/env python3
"""Сеть: поиск и чтение страниц.

    web.py search "<запрос>"     поиск через Z.ai (тот же ключ, что у модели)
    web.py read <адрес>          текст страницы, до READ_LIMIT знаков

"""
import html, json, re, sys, urllib.error, urllib.parse, urllib.request
from html.parser import HTMLParser
from _common import secrets

SEARCH = "https://api.z.ai/api/paas/v4/web_search"
READ_LIMIT = 6000
UA = "Mozilla/5.0 (lyutik; course project)"


def fail(msg):
    print(f"не вышло: {msg}")
    sys.exit(1)

def search(query):
    key = secrets().get("GLM_API_KEY")
    if not key:
        fail("нет ключа GLM — запусти tools/keys.py")
    req = urllib.request.Request(SEARCH, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        data=json.dumps({"search_engine": "search-prime", "search_query": query, "count": 8}).encode())
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        fail(f"поиск ответил {e.code}: {e.read().decode(errors='replace')[:200]}")
    except urllib.error.URLError as e:
        fail(f"нет связи с поиском: {e.reason}")
    items = res.get("search_result") or []
    if not items:
        print(f"по «{query}» ничего")
        return
    print(f"по «{query}»:")
    for i, it in enumerate(items, 1):
        snippet = " ".join((it.get("content") or "").split())[:300]
        date = f" · {it['publish_date']}" if it.get("publish_date") else ""
        print(f"[{i}] {it.get('title', '')}{date} — {it.get('link', '')}\n    {snippet}")


class Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "br", "tr"):
            self.out.append("\n")
    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
    def handle_data(self, d):
        if not self.skip:
            self.out.append(d)

def encoded(u):
    """Кириллица в адресе → %-коды, домен → punycode: иначе запрос не уйдёт."""
    netloc = u.hostname.encode("idna").decode() + (f":{u.port}" if u.port else "")
    path = urllib.parse.quote(u.path or "/", safe="/%:@!$&'()*+,;=~")
    query = urllib.parse.quote(u.query, safe="=&%+/:,;@!$'()*~")
    return urllib.parse.urlunsplit((u.scheme, netloc, path, query, ""))

def read(url):
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        fail("открываю только адреса http(s)://")
    req = urllib.request.Request(encoded(u), headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read(3_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
            ctype = r.headers.get_content_type()
    except urllib.error.HTTPError as e:
        fail(f"страница ответила {e.code}")
    except urllib.error.URLError as e:
        fail(f"нет связи: {e.reason}")
    except (TimeoutError, OSError, ValueError) as e:
        fail(f"страница не открылась: {e}")
    body = raw.decode(charset, errors="replace")
    if "html" in ctype:
        t = Text()
        t.feed(body)
        body = "".join(t.out)
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", html.unescape(body))).strip()
    print(f"{url}\n\n{text[:READ_LIMIT]}" + ("\n…(обрезано)" if len(text) > READ_LIMIT else ""))


def main():
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "search":
        search(a[1])
    elif len(a) == 2 and a[0] == "read":
        read(a[1])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
