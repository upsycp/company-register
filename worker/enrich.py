#!/usr/bin/env python3
"""Enrichment worker for India Startup Radar.

Picks companies with enrich_status = 'pending' from Supabase (highest score first) and fills in:
  - directors / founders  (free public pages: Falcon Ebiz, then Credonex; optional paid CIN API)
  - website               (registered email domain -> search API -> verified domain guess)
  - company LinkedIn      (search API)
  - founder LinkedIn      (search API, per director)
Only empty fields are filled, so anything the team typed is never overwritten.

Runs on a schedule in GitHub Actions (see .github/workflows/enrich.yml), or locally:
  python worker/enrich.py                      # process one batch
  python worker/enrich.py --test CIN "NAME"    # look up one company, print it, write nothing

Environment:
  SUPABASE_URL, SUPABASE_SERVICE_KEY           required for normal runs
  BRAVE_API_KEY | TAVILY_API_KEY | SERPER_API_KEY   optional, any one enables LinkedIn + website search
  BATCH_SIZE (default 40), MAX_FOUNDER_SEARCHES (default 2)
  DIRECTORS_API_URL, DIRECTORS_API_HEADERS, DIRECTORS_API_LIST_PATH,
  DIRECTORS_API_NAME_FIELD, DIRECTORS_API_DIN_FIELD   optional paid fallback ({cin} in the URL)
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130 Safari/537.36"}
TIMEOUT = 20
POLITE_DELAY = 1.5  # seconds between requests to the same public site
MAX_ATTEMPTS = 3
TEAM_FIELDS = ("founders", "website", "linkedin", "founder_linkedin")

FREE_MAIL = re.compile(r"gmail|yahoo|hotmail|outlook|rediff|icloud|proton|live\.|aol\.|zoho\.com$|ymail", re.I)
LEGAL = re.compile(r"\b(private|pvt\.?|limited|ltd\.?|\(opc\)|opc|llp)\b", re.I)
PARKED = re.compile(r"domain (is )?for sale|buy this domain|parked (free|domain)|sedo|godaddy|dan\.com|afternic|"
                    r"this domain (may be|is) available|hugedomains|domain has expired", re.I)
DIRECTORY_SITES = ("zaubacorp", "tofler", "thecompanycheck", "falconebiz", "credonex", "credhive", "instafinancials",
                   "indiafilings", "linkedin.com", "facebook.com", "instagram.com", "justdial", "indiamart", "crunchbase",
                   "tracxn", "x.com", "twitter.com", "youtube.com", "mca.gov.in", "economictimes", "quickcompany",
                   "companiesinn", "corpository", "allcompanylist", "registrationwala", "ambitionbox", "glassdoor",
                   "qorpiq", "vakilsearch", "startupindia", "wikipedia", "bing.com", "google.com")

session = requests.Session()
session.headers.update(UA)
_last_hit = {}


def polite_get(url, **kw):
    host = re.sub(r"^https?://([^/]+).*", r"\1", url)
    wait = POLITE_DELAY - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()
    return session.get(url, timeout=TIMEOUT, allow_redirects=True, **kw)


def short_name(name):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9& ]", " ", LEGAL.sub(" ", name))).strip()


def slug(name, upper):
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    return s.upper() if upper else s.lower()


def title_case(n):
    return " ".join(w.capitalize() for w in n.split())


# ---------------------------------------------------------------- directors

def directors_falconebiz(cin, name):
    r = polite_get(f"https://www.falconebiz.com/company/{slug(name, True)}-{cin}")
    if r.status_code != 200 or cin not in r.url:
        return []
    out = []
    for m in re.finditer(r'\{[^{}]*"@type":\s*"Person"[^{}]*\}', r.text):
        try:
            p = json.loads(m.group(0))
        except ValueError:
            continue
        din = re.search(r"#di(\d{8})", p.get("@id", ""))
        if p.get("name"):
            out.append({"name": title_case(p["name"]), "din": din.group(1) if din else None})
    return out


def directors_credonex(cin, name):
    r = polite_get(f"https://credonex.com/company/{slug(name, False)}-{cin}")
    if r.status_code != 200:
        return []
    # Next.js streams the data in chunks that can split a record; join them back up first
    text = re.sub(r'"\]\)\s*</script>\s*<script[^>]*>\s*self\.__next_f\.push\(\[\d+,"', "", r.text).replace('\\"', '"')
    out, seen = [], set()
    for m in re.finditer(r'"DirectorName":"([^"]+)","DIN":"(\d+)"(?:.{0,120}?"dateOfAppointment":"([^"]*)")?', text):
        if m.group(2) in seen:
            continue
        seen.add(m.group(2))
        out.append({"name": title_case(m.group(1)), "din": m.group(2), "appointed": m.group(3) or None})
    return out


def directors_api(cin):
    url = os.getenv("DIRECTORS_API_URL")
    if not url:
        return []
    r = session.get(url.replace("{cin}", cin), headers=json.loads(os.getenv("DIRECTORS_API_HEADERS", "{}")), timeout=30)
    r.raise_for_status()
    items = r.json()
    for part in filter(None, os.getenv("DIRECTORS_API_LIST_PATH", "").split(".")):
        items = items[int(part)] if isinstance(items, list) else items.get(part, {})
    nf, df = os.getenv("DIRECTORS_API_NAME_FIELD", "name"), os.getenv("DIRECTORS_API_DIN_FIELD", "din")
    return [{"name": title_case(str(i[nf])), "din": i.get(df)} for i in items or [] if isinstance(i, dict) and i.get(nf)]


def find_directors(cin, name, notes):
    for label, fn in (("Falcon Ebiz", lambda: directors_falconebiz(cin, name)),
                      ("Credonex", lambda: directors_credonex(cin, name)),
                      ("directors API", lambda: directors_api(cin))):
        try:
            found = fn()
        except Exception as e:  # one source failing shouldn't stop the others
            notes.append(f"{label} error: {type(e).__name__}")
            continue
        if found:
            notes.append(f"directors from {label}")
            return found
    notes.append("no directors found yet")
    return []


# ---------------------------------------------------------------- search

def search(query, n=5):
    """Return result URLs from whichever search API is configured, or [] if none."""
    try:
        if os.getenv("BRAVE_API_KEY"):
            r = session.get("https://api.search.brave.com/res/v1/web/search", timeout=TIMEOUT,
                            params={"q": query, "count": n, "country": "IN"},
                            headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"})
            r.raise_for_status()
            return [x["url"] for x in r.json().get("web", {}).get("results", [])]
        if os.getenv("TAVILY_API_KEY"):
            r = session.post("https://api.tavily.com/search", timeout=TIMEOUT,
                             json={"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": n})
            r.raise_for_status()
            return [x["url"] for x in r.json().get("results", [])]
        if os.getenv("SERPER_API_KEY"):
            r = session.post("https://google.serper.dev/search", timeout=TIMEOUT,
                             headers={"X-API-KEY": os.environ["SERPER_API_KEY"]}, json={"q": query, "gl": "in", "num": n})
            r.raise_for_status()
            return [x["link"] for x in r.json().get("organic", [])]
    except requests.RequestException as e:
        print(f"    search failed: {e}", file=sys.stderr)
    return []


def has_search():
    return any(os.getenv(k) for k in ("BRAVE_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY"))


# ---------------------------------------------------------------- website / linkedin

def site_matches(url, tokens):
    try:
        r = session.get(url, timeout=10, allow_redirects=True)
    except requests.RequestException:
        return False
    if r.status_code >= 400 or len(r.text) < 500 or PARKED.search(r.text):
        return False
    text = r.text.lower()
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S)
    return bool(title and any(t in title.group(1) for t in tokens)) and sum(t in text for t in tokens) >= max(1, len(tokens) - 1)


def find_website(name, email, notes):
    sn = short_name(name)
    tokens = [t for t in sn.lower().split() if len(t) > 2] or sn.lower().split()
    if email and "@" in email:
        domain = email.split("@")[-1].strip().lower()
        if domain and not FREE_MAIL.search(domain):
            notes.append("website from registered email")
            return f"https://{domain}"
    for url in search(f'"{sn}" India', n=8):
        if not any(d in url for d in DIRECTORY_SITES):
            home = re.match(r"https?://[^/]+", url).group(0)
            if site_matches(home, tokens):
                notes.append("website from search (name checked)")
                return home
    joined = "".join(tokens)
    for tld in (".com", ".in", ".ai", ".io", ".co", ".tech", ".co.in"):
        if site_matches(f"https://{joined}{tld}", tokens):
            notes.append("website from domain guess (name checked; verify)")
            return f"https://{joined}{tld}"
    return ""


def first_match(urls, pattern):
    return next((re.sub(r"[?#].*$", "", u) for u in urls if re.search(pattern, u)), "")


def find_company_linkedin(name):
    return first_match(search(f'site:linkedin.com/company "{short_name(name)}"'), r"linkedin\.com/company/")


def find_person_linkedin(person, company):
    urls = search(f'site:linkedin.com/in "{person}" "{short_name(company)}"', n=3)
    return first_match(urls, r"linkedin\.com/in/")


# ---------------------------------------------------------------- one company

def enrich_company(c):
    notes = []
    directors = c.get("directors") or []
    if not directors:
        directors = find_directors(c["cin"], c["name"], notes)
    patch = {"directors": directors}
    if directors and not (c.get("founders") or "").strip():
        patch["founders"] = ", ".join(d["name"] for d in directors)
    if not (c.get("website") or "").strip():
        site = find_website(c["name"], c.get("email") or "", notes)
        if site:
            patch["website"] = site
    if has_search():
        if not (c.get("linkedin") or "").strip():
            li = find_company_linkedin(c["name"])
            if li:
                patch["linkedin"] = li
                notes.append("company LinkedIn from search")
        if not (c.get("founder_linkedin") or "").strip():
            links = []
            for d in directors[: int(os.getenv("MAX_FOUNDER_SEARCHES", "2"))]:
                if not d.get("linkedin"):
                    d["linkedin"] = find_person_linkedin(d["name"], c["name"]) or None
                if d.get("linkedin"):
                    links.append(f'{d["name"]}: {d["linkedin"]}')
            if links:
                patch["founder_linkedin"] = "\n".join(links)
                notes.append(f"{len(links)} founder LinkedIn from search (verify)")
    else:
        notes.append("no search key, so LinkedIn skipped")
    patch["directors"] = directors
    patch["enrich_notes"] = "; ".join(notes)
    return patch


# ---------------------------------------------------------------- supabase

class Supabase:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
        key = os.environ["SUPABASE_SERVICE_KEY"]
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def pending(self, limit):
        r = requests.get(f"{self.url}/companies", headers=self.h, timeout=30, params={
            "select": "cin,name,email,founders,directors,website,linkedin,founder_linkedin,enrich_attempts",
            "enrich_status": "eq.pending", "order": "score.desc,added_at.desc", "limit": str(limit)})
        r.raise_for_status()
        return r.json()

    def current(self, cin):
        r = requests.get(f"{self.url}/companies", headers=self.h, timeout=30,
                         params={"select": ",".join(TEAM_FIELDS), "cin": f"eq.{cin}"})
        r.raise_for_status()
        return (r.json() or [{}])[0]

    def update(self, cin, fields):
        r = requests.patch(f"{self.url}/companies", headers=self.h, timeout=30, params={"cin": f"eq.{cin}"},
                           data=json.dumps(fields))
        r.raise_for_status()


def run_batch(limit):
    db = Supabase()
    rows = db.pending(limit)
    print(f"{len(rows)} companies to look up (search API: {'on' if has_search() else 'off'})")
    for i, c in enumerate(rows, 1):
        now = datetime.now(timezone.utc).isoformat()
        try:
            patch = enrich_company(c)
            # the team may have typed into these fields while we were searching; never overwrite that
            current = db.current(c["cin"])
            for f in TEAM_FIELDS:
                if f in patch and (current.get(f) or "").strip():
                    del patch[f]
            patch.update(enrich_status="done", enriched_at=now, enrich_attempts=(c.get("enrich_attempts") or 0) + 1)
            db.update(c["cin"], patch)
            print(f"  [{i}/{len(rows)}] {c['name']}: {patch.get('founders', '-')} | {patch.get('website', '-')} | "
                  f"{patch.get('linkedin', '-')}")
        except Exception as e:
            attempts = (c.get("enrich_attempts") or 0) + 1
            db.update(c["cin"], {"enrich_attempts": attempts, "enrich_notes": f"error: {type(e).__name__}: {e}"[:300],
                                 "enrich_status": "failed" if attempts >= MAX_ATTEMPTS else "pending"})
            print(f"  [{i}/{len(rows)}] {c['name']}: error {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", nargs=2, metavar=("CIN", "NAME"))
    ap.add_argument("--email", default="")
    a = ap.parse_args()
    if a.test:
        print(json.dumps(enrich_company({"cin": a.test[0].upper(), "name": a.test[1], "email": a.email}), indent=2))
    else:
        run_batch(int(os.getenv("BATCH_SIZE", "40")))


if __name__ == "__main__":
    main()
