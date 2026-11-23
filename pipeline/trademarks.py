"""Weekly: new consumer-brand trademark filings from IP India's Trade Marks Journal.

Every Monday IP India publishes the trademarks it has accepted, as PDFs split by class range
(https://search.ipindia.gov.in/IPOJournal/Journal/Trademark). We read the consumer classes and keep filings that
look like an early brand: filed by a private company or LLP, filed in the last 18 months, and marked
"Proposed to be Used" (not launched yet). A filing by a company already in the register is tagged on that
company; the rest become "Trademark filings" records of their own.
"""
import io, json, re, time
from datetime import datetime, timedelta

import requests
from pypdf import PdfReader

LIST_URL = "https://search.ipindia.gov.in/IPOJournal/Journal/Trademark"
PDF_URL = "https://search.ipindia.gov.in/IPOJournal/Journal/ViewJournal"
CLASS_CAT = {3: "beauty", 18: "footwear", 25: "fashion", 28: "kids", 29: "food", 30: "food", 32: "drinks",
             33: "alcohol"}
MAX_AGE_DAYS = 548
UA = {"User-Agent": "Mozilla/5.0 (compatible; company-register/1.0)"}
HINDI = re.compile(r"[ऀ-ॿ]")
LEGAL = re.compile(r"\b(m/?s|private|pvt|limited|ltd|opc|llp)\b|[^a-z0-9 ]")


def norm(name):
    return " ".join(LEGAL.sub(" ", name.lower().replace("m/s", " ")).split())


def latest_journals():
    """[(journal_no, date_str, [(file_name, lo, hi), ...]), ...] newest first, from the journal list page."""
    html = requests.get(LIST_URL, headers=UA, timeout=60).text
    out = {}
    for fn in re.findall(r'name="FileName" value="([^"]+)"', html):
        m = re.search(r"\\TMR\\(\d{4})\\(\d+)\\Class\s*(\d+)\s*-\s*(\d+)\.pdf$", fn, re.I)
        if m:
            out.setdefault(int(m.group(2)), []).append((fn.replace("&amp;", "&"), int(m.group(3)), int(m.group(4))))
    return sorted(out.items(), reverse=True)


def wanted(files):
    return [f for f in files if any(f[1] <= c <= f[2] for c in CLASS_CAT)]


def lines(text):
    return [l.strip() for l in text.splitlines() if l.strip() and re.search(r"[A-Za-z0-9]", l) and not HINDI.search(l)]


def after(ls, label):
    i = next((k for k, l in enumerate(ls) if l.endswith(label)), None)
    return ls[i + 1] if i is not None and i + 1 < len(ls) else ""


def parse_pdf(data):
    recs = []
    for p in PdfReader(io.BytesIO(data)).pages:
        t = p.extract_text() or ""
        if "/Application Number" not in t:
            continue
        ls = lines(t)
        no = re.search(r"Application Number:\s*(\d+)", t)
        filed = re.search(r"Date of Filing:\s*(\d\d-\d\d-\d{4})", t)
        cls = after(ls, "/Class")
        gi = next((k for k, l in enumerate(ls) if l == "Goods/Services"), None)
        ui = next((k for k, l in enumerate(ls) if l.endswith("/ Used Since")), None)
        goods = " ".join(ls[gi + 1:ui]) if gi is not None and ui else ""
        goods = re.sub(r"^Class \d+:\s*", "", goods)
        mark = after(ls, "/Advertised Trade Mark")
        recs.append({"no": no.group(1) if no else "", "filed": filed.group(1) if filed else "",
                     "who": after(ls, "/ Proprietor/Applicant"), "type": after(ls, "/ Type of Applicant"),
                     "cls": int(cls) if cls.isdigit() else 0, "goods": goods[:240],
                     "since": after(ls, "/ Used Since"), "where": after(ls, "/ Jurisdiction"),
                     "mark": "" if re.fullmatch(r"\d+", mark or "") else mark})
    return recs


def keep(rec, today=None):
    """A recent filing by a private company or LLP, for a consumer brand that isn't on sale yet."""
    if rec["cls"] not in CLASS_CAT or "Proposed" not in rec["since"]:
        return False
    if not re.search(r"\b(private|pvt|llp|opc)\b", rec["who"], re.I) or re.search(r"\b(co\.,? ltd|fz|inc|gmbh)\b", rec["who"], re.I):
        return False
    try:
        return (today or datetime.now()) - datetime.strptime(rec["filed"], "%d-%m-%Y") < timedelta(days=MAX_AGE_DAYS)
    except ValueError:
        return False


def journal_date(pdf_bytes):
    m = re.search(r"Date:\s*\n?\s*(\d\d/\d\d/\d{4})", PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text() or "")
    return datetime.strptime(m.group(1), "%d/%m/%Y").strftime("%Y-%m-%d") if m else datetime.now().strftime("%Y-%m-%d")


def process_journal(jn, files, log=print):
    """Download and read one journal's consumer-class PDFs. Returns kept filings (plain dicts)."""
    kept, jd = [], None
    for fn, lo, hi in wanted(files):
        r = None
        for attempt in range(3):
            try:
                r = requests.post(PDF_URL, data={"FileName": fn}, headers=UA, timeout=900)
                if r.status_code == 200 and r.content.startswith(b"%PDF"):
                    break
            except requests.RequestException:
                pass
            time.sleep(20 * (attempt + 1))
        if r is None or not r.content.startswith(b"%PDF"):
            log(f"journal {jn} class {lo}-{hi}: download failed"); continue
        jd = jd or journal_date(r.content)
        recs = parse_pdf(r.content)
        k = [dict(x, jn=jn, jd=jd) for x in recs if keep(x)]
        kept += k
        log(f"journal {jn} class {lo}-{hi}: {len(recs)} applications, {len(k)} kept")
    return kept


def merge(data, filings):
    """Add kept filings to the register in place. Returns (tagged_existing, new_records)."""
    by_name = {norm(r["n"]): r for r in data if not r.get("b")}
    tagged = added = 0
    for x in filings:
        if not keep(x):                      # re-check age at merge time
            continue
        filed = datetime.strptime(x["filed"], "%d-%m-%Y").strftime("%Y-%m-%d")
        tm = {"mk": x["mark"], "cls": x["cls"], "g": x["goods"], "fd": filed, "no": x["no"], "jn": x["jn"], "jd": x["jd"]}
        k = norm(x["who"])
        r = by_name.get(k)
        if r is None:
            r = {"c": "t-" + re.sub(r"[^a-z0-9]+", "-", k).strip("-")[:56], "n": x["who"].strip(), "cat": CLASS_CAT[x["cls"]],
                 "t": 1, "loc": x["where"].title()}
            data.append(r); by_name[k] = r; added += 1
        if any(old.get("no") == x["no"] for old in r.setdefault("tm", [])):
            continue
        if not r.get("t") and not r["tm"]:
            tagged += 1
        r["tm"].append(tm)
        if r.get("t"):   # file a brand-only record under its most common class
            cs = [f["cls"] for f in r["tm"]]
            r["cat"] = CLASS_CAT[max(set(cs), key=cs.count)]
            r["fd"] = max(f["fd"] for f in r["tm"])
    return tagged, added


def run(data, state, store, log=print):
    """Nightly: read any new journal (one appears each Monday) and merge results saved by the backfill job."""
    done = set(state.setdefault("done", []))
    journals = latest_journals()
    if not done:                       # first run: this week's journal; older ones come from the backfill job
        done = {jn for jn, _ in journals[1:]}
    filings = []
    for jn, files in journals[:4]:
        if jn not in done:
            filings += process_journal(jn, files, log)
            done.add(jn)
    for name in store.list("tm-backfill/"):     # results saved by .github/workflows/backfill-trademarks.yml
        part = json.loads(store.get(f"tm-backfill/{name}") or b"{}")
        filings += part.get("filings", [])
        done.add(part.get("jn"))
        store.move(f"tm-backfill/{name}", f"tm-merged/{name}")
        log(f"merged backfill results for journal {part.get('jn')}: {len(part.get('filings', []))} filings")
    state["done"] = sorted(x for x in done if x)
    return merge(data, filings)
