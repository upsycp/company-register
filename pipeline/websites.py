"""Nightly website check, and automatic Picks.

For each company (best matches first) it either confirms the website we already have, or looks for one:
a web search if a search key is set (Brave, Tavily or Serper), then likely domains (brandname.com, .in, .ai…).
A site only counts as verified when it loads, isn't a parked/for-sale page, and shows the company's name in
its title and text. A company with a verified site and a good category score joins Picks automatically.
"""
import re, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import enrich

PICK_MIN_SCORE = 8            # category score needed for an automatic pick
RETRY_DAYS = 30               # look again for companies with no site after this long
GENERIC = re.compile(r"\b(private|limited|pvt|ltd|opc|llp|india|indian|global|international|ventures?|enterprises?|"
                     r"technologies|technology|tech|solutions?|services|innovations?|labs?|industries|group|company|"
                     r"co|and|the|of|brands?|retail|foods?|beverages|products|consumer|lifestyle|studios?|works)\b")
TLDS = (".com", ".in", ".co.in", ".ai", ".co", ".io", ".shop", ".store")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def core(name):
    """Distinctive name parts: every word except legal/generic ones, short ones like 'KV' or '30D' included."""
    words = re.findall(r"[a-z0-9]+", enrich.LEGAL.sub(" ", name.lower()))
    return [w for w in words if not GENERIC.fullmatch(w)]


def guesses(name):
    c = core(name)
    stems = ["".join(c)]
    if len(c) > 2:
        stems.append("".join(c[:2]))
    return [f"https://{st}{t}" for st in stems if 5 <= len(st) <= 30 for t in TLDS]


def check(url, name):
    """True only if the page is live and clearly this company's: its address carries the full distinctive
    name (or the first two words of it, with the whole name in the page title)."""
    c = core(name)
    full, two = "".join(c), "".join(c[:2])
    if len(full) < 5:
        return False                     # too generic to tell apart (e.g. "KV Brands" -> "kv")
    try:
        r = enrich.session.get(url, timeout=10, allow_redirects=True)
    except Exception:  # noqa: BLE001
        return False
    if r.status_code >= 400 or len(r.text) < 500 or enrich.PARKED.search(r.text):
        return False
    host = re.sub(r"^www\.", "", re.sub(r"^https?://([^/:]+).*", r"\1", r.url.lower()))
    stem = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
    m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
    title = re.sub(r"\s+", " ", m.group(1).lower()) if m else ""
    words = [w for w in c if len(w) >= 3] or c
    if full in stem and any(w in title for w in words):
        return True
    return len(c) > 2 and len(two) >= 6 and two in stem and all(w in title for w in words)


def find(name):
    if len("".join(core(name))) < 5:
        return "", ""
    for url in enrich.search(f'"{enrich.short_name(name)}" India', n=8):
        if not any(d in url for d in enrich.DIRECTORY_SITES):
            home = re.match(r"https?://[^/]+", url).group(0)
            if check(home, name):
                return home, "found by web search, name checked on the site"
    for url in guesses(name):
        if check(url, name):
            return url, "found by domain guess, name checked on the site"
    return "", ""


def run(data, cache, deadline, threads=8):
    """cache: {cin: {"w", "ok", "how", "at"}}. Updates records in place; returns (checked, verified, new_picks)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS)).isoformat()
    todo = [r for r in data if not r.get("b") and not r.get("wv")
            and (r.get("w") and r["c"] not in cache or cache.get(r["c"], {}).get("at", "") < cutoff)]
    # companies with an unchecked site first, then never-searched ones, best category matches first
    todo.sort(key=lambda r: (not r.get("w"), r["c"] in cache, -r.get("sc", 0)))
    lock, stats = threading.Lock(), [0, 0, 0]
    print(f"{len(todo)} companies need a website check", flush=True)

    def work(r):
        if time.time() > deadline:
            return
        url, how = "", ""
        if r.get("w"):
            u = "https://" + r["w"]
            if check(u, r["n"]):
                url, how = u, "listed site, name checked on the site"
        if not url:
            url, how = find(r["n"])
        with lock:
            stats[0] += 1
            cache[r["c"]] = {"w": url, "ok": bool(url), "how": how, "at": now()}
            if url:
                stats[1] += 1
                r["w"] = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
                r["wv"] = 1
                r["e"] = how[0].upper() + how[1:]
                if not r.get("p") and r.get("sc", 0) >= PICK_MIN_SCORE:
                    r["p"] = 1; r["pa"] = now()[:10]; stats[2] += 1

    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(work, todo))
    return tuple(stats)
