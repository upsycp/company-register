#!/usr/bin/env python3
"""Indian early-stage startup tracker.

Pipeline:
  1. ingest  - read MCA "Master Details of Companies Registered" monthly sheet(s)
  2. filter  - score each company on how startup-like it is (NIC code from CIN, name, capital, class)
  3. enrich  - directors (founders) by CIN, website, LinkedIn company page, founder LinkedIn
  4. export  - write an Excel/CSV list of new early-stage startups

State lives in tracker.db (SQLite), so every run only processes and reports companies it hasn't seen.

Usage:
  python tracker.py run                 # ingest everything in inbox/, enrich, export new startups
  python tracker.py ingest FILE.xlsx    # just ingest + score one file
  python tracker.py enrich [--limit N]  # enrich shortlisted companies not yet enriched
  python tracker.py export [--all]      # export (default: not-yet-exported only)
  python tracker.py import-directors directors.csv   # columns: cin, name, din (optional)
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "tracker.db"
INBOX = ROOT / "inbox"
PROCESSED = INBOX / "processed"
OUTPUT = ROOT / "output"

SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "4"))
HTTP_TIMEOUT = 10
UA = {"User-Agent": "Mozilla/5.0 (startup-tracker)"}

# ---------------------------------------------------------------- scoring rules

# NIC-2008 prefixes (from CIN digits 2-6) that look like tech / deep-tech / new-economy.
TECH_NIC = {
    "62": 4,    # computer programming, consultancy
    "631": 4,   # data processing, hosting, web portals
    "582": 4,   # software publishing
    "72": 4,    # scientific R&D (incl. biotech)
    "261": 2, "262": 3, "263": 2, "265": 2, "267": 2,  # electronics, computers, comms, instruments
    "2790": 1, "2930": 2,  # electrical equipment, EV components
    "3250": 2,  # medical devices
    "21": 1,    # pharma
    "6419": 1, "6499": 1, "6619": 2,  # fintech-ish financial services
    "7020": 1, "7310": 1, "7410": 1,  # consulting, advertising, design
    "8550": 2,  # education support (edtech)
    "5320": 1,  # courier (quick-commerce/logistics tech)
    "4791": 2,  # retail via internet (e-commerce)
}
# NIC-2004 codes still appear on some CINs.
TECH_NIC_2004 = {"722": 4, "721": 4, "723": 3, "724": 3, "729": 3, "73": 3}

NAME_POSITIVE = {
    4: ["technolog", "tech ", "techno", "labs", " ai ", "artificial intelligence", "robotic", "software",
        "analytics", "fintech", "biotech", "saas", "deeptech", "quantum", "semiconductor", "drone",
        "aerospace", "space", "blockchain", "cyber", "automat", "intelligen"],
    2: ["digital", "data", "cloud", "health", "innovat", "mobility", "energy", "electric", " ev ", "edtech",
        "learning", "platform", "network", "systems", "studio", "ventures", "app", "devices", "climate",
        "genomic", "therapeut", "foods", "commerce", "logistic"],
}
NAME_NEGATIVE = ["trader", "trading", "construction", "builder", "realty", "real estate", "developers", "infra",
                 "jewel", "textile", "hotel", "restaurant", "transport", "petroleum", "minerals", "mining",
                 "agro", "farm", "dairy", "steel", "cement", "chit", "nidhi", "finance & invest", "estate",
                 "properties", "housing", "exports", "imports", "garments", "enterprises", "hospitality",
                 "travels", "tours", "marbles", "granite", "rice", "cotton", "spinning", "chemicals"]

STARTUP_HUB_STATES = {"KA", "MH", "DL", "HR", "TG", "TN", "UP", "GJ", "WB", "KL", "RJ"}

LEGAL_SUFFIX = re.compile(
    r"\b(private|pvt\.?|limited|ltd\.?|opc|\(opc\)|llp|india|company|co\.?)\b", re.I)

CIN_RE = re.compile(r"^([LU])(\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})$")


def parse_cin(cin):
    m = CIN_RE.match(str(cin).strip().upper())
    if not m:
        return None
    listed, nic, state, year, cls, _ = m.groups()
    return {"listed": listed, "nic": nic, "state": state, "year": int(year), "class": cls}


def nic_score(nic, year):
    table = TECH_NIC_2004 if year < 2009 else TECH_NIC
    best = 0
    for prefix, pts in table.items():
        if nic.startswith(prefix):
            best = max(best, pts)
    return best


def score_company(row):
    """Return (score, reasons) - higher means more startup-like."""
    reasons = []
    info = parse_cin(row["cin"])
    if not info:
        return -99, ["bad CIN"]
    if info["listed"] == "L":
        return -99, ["listed company"]
    if info["class"] == "NPL":
        return -99, ["section 8 / non-profit"]
    if info["class"] in ("GOI", "SGC", "GAP", "GAT", "FTC", "FLC"):
        return -99, [f"class {info['class']}"]

    score = 0
    pts = nic_score(info["nic"], info["year"])
    if pts:
        score += pts
        reasons.append(f"NIC {info['nic']} (+{pts})")

    name = f" {row['name'].lower()} "
    for pts, words in NAME_POSITIVE.items():
        hit = next((w for w in words if w in name), None)
        if hit:
            score += pts
            reasons.append(f"name '{hit.strip()}' (+{pts})")
            break
    neg = next((w for w in NAME_NEGATIVE if w in name), None)
    if neg:
        score -= 4
        reasons.append(f"name '{neg}' (-4)")

    if info["class"] == "PTC":
        score += 1
        reasons.append("private ltd (+1)")
    elif info["class"] == "OPC":
        reasons.append("OPC (+0)")

    paid = row.get("paid_up_capital") or 0
    if 0 < paid <= 1_000_000:
        score += 1
        reasons.append("paid-up <= 10L (+1)")
    if info["state"] in STARTUP_HUB_STATES:
        score += 1
        reasons.append(f"hub state {info['state']} (+1)")
    return score, reasons


# ---------------------------------------------------------------- ingest

# MCA renames columns between months, so map by fuzzy header match.
COLUMN_ALIASES = {
    "cin": ["cin", "corporate identification number", "company cin", "llpin"],
    "name": ["company name", "company_name", "name of the company", "name"],
    "incorporated_on": ["date of incorporation", "date_of_registration", "date of registration",
                        "incorporation date", "doi"],
    "state": ["state", "registered_state", "company state code"],
    "roc": ["roc", "roc code", "company_roc_code"],
    "category": ["category", "company category", "company_category"],
    "sub_category": ["sub category", "sub-category", "company_subcategory", "company sub category"],
    "company_class": ["class", "company class", "company_class"],
    "authorized_capital": ["authorized capital", "authorised capital", "authorized_capital",
                           "authorized cap"],
    "paid_up_capital": ["paid up capital", "paidup capital", "paidup_capital", "paid-up capital",
                        "paid_up_capital"],
    "activity": ["activity description", "principal business activity", "industrial activity",
                 "principal_business_activity_as_per_cin", "activity", "business activity"],
    "address": ["registered office address", "registered_office_address", "address"],
    "email": ["email", "email id", "email_addr"],
}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def find_header_row(raw):
    """MCA sheets sometimes have title rows above the header; find the row containing 'cin'."""
    for i in range(min(15, len(raw))):
        cells = [_norm(c) for c in raw.iloc[i].tolist()]
        if any(c in ("cin", "llpin", "corporate identification number") for c in cells):
            return i
    return 0


def map_columns(columns):
    mapping = {}
    normed = {c: _norm(c) for c in columns}
    for field, aliases in COLUMN_ALIASES.items():
        al = [_norm(a) for a in aliases]
        exact = next((c for c, n in normed.items() if n in al), None)
        loose = next((c for c, n in normed.items() if any(a in n for a in al if len(a) > 3)), None)
        col = exact or loose
        if col is not None and col not in mapping.values():
            mapping[field] = col
    return mapping


def to_number(v):
    if pd.isna(v):
        return 0
    s = re.sub(r"[^\d.]", "", str(v))
    return float(s) if s else 0


def read_mca_file(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        sheets = {"csv": pd.read_csv(path, header=None, dtype=str)}
    else:
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    frames = []
    for sheet_name, raw in sheets.items():
        if raw.empty:
            continue
        h = find_header_row(raw)
        df = raw.iloc[h + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[h].tolist()]
        mapping = map_columns(df.columns)
        if "cin" not in mapping or "name" not in mapping:
            print(f"  skip sheet '{sheet_name}': no CIN/name column (headers: {list(df.columns)[:8]})")
            continue
        out = pd.DataFrame({f: df[c] for f, c in mapping.items()})
        out["source_sheet"] = sheet_name
        frames.append(out)
        print(f"  sheet '{sheet_name}': {len(out)} rows, mapped {sorted(mapping)}")
    if not frames:
        raise SystemExit(f"No usable sheet in {path}")
    df = pd.concat(frames, ignore_index=True)
    df = df[df["cin"].notna()]
    df["cin"] = df["cin"].str.strip().str.upper()
    df = df[df["cin"].str.match(CIN_RE.pattern)]
    for c in ("authorized_capital", "paid_up_capital"):
        if c in df:
            df[c] = df[c].map(to_number)
    return df.drop_duplicates("cin")


# ---------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  cin TEXT PRIMARY KEY, name TEXT, incorporated_on TEXT, state TEXT, roc TEXT,
  company_class TEXT, category TEXT, sub_category TEXT, nic TEXT, activity TEXT,
  authorized_capital REAL, paid_up_capital REAL, address TEXT, email TEXT,
  score INTEGER, score_reasons TEXT, shortlisted INTEGER,
  source_file TEXT, ingested_at TEXT,
  website TEXT, linkedin TEXT, enriched_at TEXT, enrich_notes TEXT, exported_at TEXT
);
CREATE TABLE IF NOT EXISTS directors (
  cin TEXT, name TEXT, din TEXT, designation TEXT, linkedin TEXT, source TEXT,
  PRIMARY KEY (cin, name)
);
"""


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def ingest(path):
    print(f"Ingesting {path}")
    df = read_mca_file(path)
    con = db()
    now = datetime.now().isoformat(timespec="seconds")
    new = short = 0
    for rec in df.to_dict("records"):
        rec = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in rec.items()}
        rec["name"] = str(rec["name"]).strip()
        score, reasons = score_company(rec)
        info = parse_cin(rec["cin"]) or {}
        cur = con.execute(
            """INSERT OR IGNORE INTO companies (cin, name, incorporated_on, state, roc, company_class,
               category, sub_category, nic, activity, authorized_capital, paid_up_capital, address, email,
               score, score_reasons, shortlisted, source_file, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec["cin"], rec["name"], str(rec.get("incorporated_on") or "")[:10],
             rec.get("state") or info.get("state"), rec.get("roc"),
             rec.get("company_class") or info.get("class"), rec.get("category"), rec.get("sub_category"),
             info.get("nic"), rec.get("activity"), rec.get("authorized_capital"), rec.get("paid_up_capital"),
             rec.get("address"), rec.get("email"), score, "; ".join(reasons),
             int(score >= SCORE_THRESHOLD), Path(path).name, now))
        if cur.rowcount:
            new += 1
            short += score >= SCORE_THRESHOLD
    con.commit()
    print(f"  {len(df)} companies in file, {new} new, {short} shortlisted (score >= {SCORE_THRESHOLD})")


# ---------------------------------------------------------------- enrichment providers

def search(query, num=5):
    """Web search via Serper.dev (Google results). Returns list of {title, link, snippet}."""
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return []
    r = requests.post("https://google.serper.dev/search", timeout=HTTP_TIMEOUT,
                      headers={"X-API-KEY": key, "Content-Type": "application/json"},
                      json={"q": query, "gl": "in", "num": num})
    r.raise_for_status()
    return r.json().get("organic", [])


def short_name(name):
    s = LEGAL_SUFFIX.sub(" ", name)
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9& ]", " ", s)).strip()


def get_json_path(obj, path):
    for part in path.split(".") if path else []:
        obj = obj[int(part)] if isinstance(obj, list) else obj.get(part, {})
    return obj


def fetch_directors(cin):
    """Directors by CIN from a configurable company-data API.

    MCA's own master-data lookup is captcha-protected, so use a licensed provider
    (e.g. Probe42, Surepass, Signzy, Tofler API, Attestr). Configure in .env:
      DIRECTORS_API_URL   e.g. https://api.provider.com/companies/{cin}/directors
      DIRECTORS_API_HEADERS  JSON, e.g. {"x-api-key": "..."}
      DIRECTORS_API_LIST_PATH  dot path to the list in the JSON response, e.g. data.directors
      DIRECTORS_API_NAME_FIELD / DIRECTORS_API_DIN_FIELD / DIRECTORS_API_ROLE_FIELD
    """
    url = os.getenv("DIRECTORS_API_URL")
    if not url:
        return []
    headers = json.loads(os.getenv("DIRECTORS_API_HEADERS", "{}"))
    method = os.getenv("DIRECTORS_API_METHOD", "GET").upper()
    if method == "POST":
        body = json.loads(os.getenv("DIRECTORS_API_BODY", '{"cin": "{cin}"}').replace("{cin}", cin))
        r = requests.post(url, headers=headers, json=body, timeout=30)
    else:
        r = requests.get(url.replace("{cin}", cin), headers=headers, timeout=30)
    r.raise_for_status()
    items = get_json_path(r.json(), os.getenv("DIRECTORS_API_LIST_PATH", "")) or []
    nf = os.getenv("DIRECTORS_API_NAME_FIELD", "name")
    df = os.getenv("DIRECTORS_API_DIN_FIELD", "din")
    rf = os.getenv("DIRECTORS_API_ROLE_FIELD", "designation")
    return [{"name": str(i.get(nf, "")).strip().title(), "din": i.get(df), "designation": i.get(rf)}
            for i in items if isinstance(i, dict) and i.get(nf)]


PARKED = re.compile(r"domain (is )?for sale|buy this domain|parked (free|domain)|sedo|godaddy|dan\.com|"
                    r"afternic|this domain (may be|is) available|hugedomains|domain has expired")


def page_mentions(url, tokens):
    try:
        r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return False
        text = r.text.lower()
        if PARKED.search(text) or len(text) < 500:
            return False
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S)
        if not title or not any(t in title.group(1) for t in tokens):
            return False
        return sum(t in text for t in tokens) >= max(1, len(tokens) - 1)
    except requests.RequestException:
        return False


SKIP_DOMAINS = ("zaubacorp", "tofler", "thecompanycheck", "falconebiz", "instafinancials", "indiafilings",
                "linkedin.com", "facebook.com", "instagram.com", "justdial", "indiamart", "crunchbase",
                "tracxn", "x.com", "twitter.com", "youtube.com", "mca.gov.in", "economictimes", "quickcompany",
                "companiesinn", "corpository", "allcompanylist", "registrationwala", "ambitionbox", "glassdoor")


def find_website(name, email):
    sn = short_name(name)
    tokens = [t for t in sn.lower().split() if len(t) > 2] or sn.lower().split()

    # 1. company email on a custom domain is the strongest signal
    if email and "@" in str(email):
        domain = str(email).split("@")[-1].strip().lower()
        if domain and not re.search(r"gmail|yahoo|hotmail|outlook|rediff|icloud|proton|live\.", domain):
            return f"https://{domain}", "from registered email"

    # 2. search
    for res in search(f'"{sn}" official website', num=8):
        link = res.get("link", "")
        if link and not any(d in link for d in SKIP_DOMAINS):
            return re.match(r"https?://[^/]+", link).group(0), "search"

    # 3. guess domains and verify the page mentions the name
    slug = "".join(tokens)
    for tld in (".com", ".in", ".ai", ".io", ".co", ".tech", ".co.in"):
        url = f"https://{slug}{tld}"
        if page_mentions(url, tokens):
            return url, "domain guess (verified)"
    return None, "not found"


def find_linkedin_company(name):
    sn = short_name(name)
    for res in search(f'site:linkedin.com/company "{sn}"'):
        if "linkedin.com/company/" in res.get("link", ""):
            return res["link"]
    return None


def find_linkedin_person(person, company):
    for res in search(f'site:linkedin.com/in "{person}" "{short_name(company)}"', num=3):
        if "linkedin.com/in/" in res.get("link", ""):
            return res["link"]
    for res in search(f'site:linkedin.com/in "{person}" founder', num=3):
        if "linkedin.com/in/" in res.get("link", ""):
            return res["link"]
    return None


def enrich(limit=None):
    con = db()
    rows = con.execute("SELECT * FROM companies WHERE shortlisted=1 AND enriched_at IS NULL "
                       "ORDER BY score DESC, incorporated_on DESC" + (f" LIMIT {int(limit)}" if limit else "")
                       ).fetchall()
    has_search = bool(os.getenv("SERPER_API_KEY"))
    has_dir = bool(os.getenv("DIRECTORS_API_URL"))
    print(f"Enriching {len(rows)} companies (search API: {'on' if has_search else 'OFF'}, "
          f"directors API: {'on' if has_dir else 'OFF'})")
    for i, c in enumerate(rows, 1):
        notes = []
        try:
            for d in fetch_directors(c["cin"]):
                con.execute("INSERT OR IGNORE INTO directors (cin, name, din, designation, source) "
                            "VALUES (?,?,?,?,?)", (c["cin"], d["name"], d["din"], d["designation"], "api"))
        except Exception as e:  # keep going on provider errors
            notes.append(f"directors error: {e}")

        website, how = find_website(c["name"], c["email"])
        notes.append(f"website {how}")
        linkedin = find_linkedin_company(c["name"]) if has_search else None

        if has_search:
            for d in con.execute("SELECT name FROM directors WHERE cin=? AND linkedin IS NULL", (c["cin"],)):
                url = find_linkedin_person(d["name"], c["name"])
                if url:
                    con.execute("UPDATE directors SET linkedin=? WHERE cin=? AND name=?",
                                (url, c["cin"], d["name"]))
        con.execute("UPDATE companies SET website=?, linkedin=?, enriched_at=?, enrich_notes=? WHERE cin=?",
                    (website, linkedin, datetime.now().isoformat(timespec="seconds"), "; ".join(notes), c["cin"]))
        con.commit()
        print(f"  [{i}/{len(rows)}] {c['name']}: web={website or '-'} li={'yes' if linkedin else '-'}")
        time.sleep(0.3)


def import_directors(path):
    df = pd.read_csv(path, dtype=str)
    df.columns = [_norm(c) for c in df.columns]
    con = db()
    for r in df.to_dict("records"):
        con.execute("INSERT OR IGNORE INTO directors (cin, name, din, designation, source) VALUES (?,?,?,?,?)",
                    (r["cin"].strip().upper(), r["name"].strip().title(), r.get("din"), r.get("designation"),
                     "csv"))
    con.commit()
    print(f"Imported {len(df)} director rows")


# ---------------------------------------------------------------- export

def export(include_all=False):
    con = db()
    where = "shortlisted=1" + ("" if include_all else " AND exported_at IS NULL")
    rows = con.execute(f"SELECT * FROM companies WHERE {where} ORDER BY score DESC, incorporated_on DESC").fetchall()
    if not rows:
        print("Nothing new to export.")
        return None
    out = []
    for c in rows:
        dirs = con.execute("SELECT name, din, linkedin FROM directors WHERE cin=?", (c["cin"],)).fetchall()
        out.append({
            "Company": c["name"], "CIN": c["cin"], "Incorporated": c["incorporated_on"], "State": c["state"],
            "Class": c["company_class"], "NIC": c["nic"], "Activity": c["activity"],
            "Paid-up capital": c["paid_up_capital"], "Score": c["score"], "Why": c["score_reasons"],
            "Founders / directors": ", ".join(d["name"] for d in dirs),
            "Founder LinkedIn": "\n".join(f"{d['name']}: {d['linkedin']}" for d in dirs if d["linkedin"]),
            "Website": c["website"], "How found": c["enrich_notes"], "Company LinkedIn": c["linkedin"], "Email": c["email"],
            "Address": c["address"],
            "MCA lookup": "https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS.html",
        })
    OUTPUT.mkdir(exist_ok=True)
    path = OUTPUT / f"early_stage_startups_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
    df = pd.DataFrame(out)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="Startups")
        ws = xw.sheets["Startups"]
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            width = min(60, max(len(str(cell.value or "")) for cell in col[:200]) + 2)
            ws.column_dimensions[col[0].column_letter].width = width
    if not include_all:
        now = datetime.now().isoformat(timespec="seconds")
        con.executemany("UPDATE companies SET exported_at=? WHERE cin=?", [(now, c["cin"]) for c in rows])
        con.commit()
    print(f"Exported {len(out)} startups -> {path}")
    return path


# ---------------------------------------------------------------- cli

def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def run(limit=None):
    INBOX.mkdir(exist_ok=True)
    PROCESSED.mkdir(exist_ok=True)
    files = sorted(p for p in INBOX.iterdir() if p.suffix.lower() in (".xlsx", ".xls", ".csv"))
    if not files:
        print(f"No MCA files in {INBOX}. Download the monthly sheet from MCA and drop it there.")
    for f in files:
        ingest(f)
        f.rename(PROCESSED / f.name)
    enrich(limit)
    export()


def main():
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("--limit", type=int)
    p = sub.add_parser("ingest"); p.add_argument("file")
    p = sub.add_parser("enrich"); p.add_argument("--limit", type=int)
    p = sub.add_parser("export"); p.add_argument("--all", action="store_true")
    p = sub.add_parser("import-directors"); p.add_argument("file")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.limit)
    elif a.cmd == "ingest":
        ingest(a.file)
    elif a.cmd == "enrich":
        enrich(a.limit)
    elif a.cmd == "export":
        export(a.all)
    elif a.cmd == "import-directors":
        import_directors(a.file)


if __name__ == "__main__":
    sys.exit(main())
