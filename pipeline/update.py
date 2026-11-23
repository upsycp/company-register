#!/usr/bin/env python3
"""Keep the register up to date. Runs daily on GitHub Actions (and can be run by hand).

1. Reads any MCA workbooks the team uploaded on the site (storage: inbox/).
2. Files each new private company into a category; the likely startups join the register.
3. Looks up founders for companies that don't have them yet, for up to TIME_BUDGET_MIN minutes.
4. Publishes the updated register (data.json) back to the private bucket.

Needs SUPABASE_URL and SUPABASE_SERVICE_KEY. Nothing here is ever written to the repository.
"""
import json, os, re, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "worker"))
from categorize import CAT_MIN, best  # noqa: E402
from mca import CIN_RE, STATES, read_report  # noqa: E402
from store import Store  # noqa: E402
import enrich  # noqa: E402

MIN_SCORE = int(os.environ.get("MIN_SCORE", "6"))
BUDGET = float(os.environ.get("TIME_BUDGET_MIN", "40")) * 60
RETRY_DAYS = int(os.environ.get("RETRY_DAYS", "10"))   # directors of new companies take weeks to appear
enrich.POLITE_DELAY = 1.0
start = time.time()
now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731

st = Store()
data = st.get_json("data.json", [])
cache = st.get_json("founders.json", {})
known = {r["c"] for r in data}
print(f"register: {len(data)} records, founder cache: {len(cache)}", flush=True)


def publish():
    data.sort(key=lambda r: (-r.get("sc", 0), r["n"]))
    st.put("data.json", "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in data) + "\n]\n")
    st.put("founders.json", json.dumps(cache))
    months = sorted({r["m"] for r in data if r.get("m")})
    st.put("meta.json", json.dumps({"updated": now(), "count": len(data), "months": months}))


# ---------------------------------------------------------------- 1-2. new MCA files
added = 0
for name in st.list("inbox/"):
    src = f"inbox/{name}"
    if not name.lower().endswith(".xlsx"):
        st.move(src, f"rejected/{name}"); print("not an xlsx, moved to rejected/:", name); continue
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        tmp.write(st.get(src)); tmp.flush()
        try:
            rows = read_report(tmp.name)
        except Exception as e:  # noqa: BLE001
            st.move(src, f"rejected/{name}"); print("couldn't read", name, e); continue
    n_file = 0
    for r in rows:
        cin = (r.get("cin") or "").upper().strip(); m = CIN_RE.match(cin)
        if not m or cin in known:
            continue
        listed, nic, state, _, cls, _ = m.groups()
        if listed == "L" or cls not in ("PTC", "OPC"):
            continue
        reg = str(r.get("date of registration", ""))[:10]
        mm = re.match(r"(\d{2})-(\d{2})-(\d{4})", reg) or re.match(r"(\d{4})-(\d{2})-(\d{2})", reg)
        if not mm:
            continue
        y, mo, d = mm.groups() if len(mm.group(1)) == 4 else mm.groups()[::-1]
        b = best(cin, r.get("company name", "").strip(), nic, state, cls)
        if not b or b[0] < max(MIN_SCORE, CAT_MIN.get(b[1], 0)):
            continue
        data.append({"c": cin, "n": r.get("company name", "").strip(), "cat": b[1], "sc": b[0], "m": f"{y}-{mo}",
                     "r": f"{d}-{mo}-{y}", "s": STATES.get(state, state), "nic": nic})
        known.add(cin); n_file += 1
    st.move(src, f"processed/{name}")
    added += n_file
    print(f"{name}: {len(rows)} rows, {n_file} new companies filed", flush=True)
if added:
    publish()   # get new companies on the site before the slow founder lookup

# ---------------------------------------------------------------- 3. founders
cutoff = (datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS)).isoformat()
todo = [r for r in data if not r.get("b") and not r.get("f") and cache.get(r["c"], {}).get("at", "") < cutoff
        and not cache.get(r["c"], {}).get("directors")]
todo.sort(key=lambda r: (r["c"] in cache, -r.get("sc", 0)))   # never-tried first, best matches first
print(f"{len(todo)} companies need founders", flush=True)
lock = threading.Lock(); done = [0, 0]; last_pub = [time.time()]


def work(r):
    if time.time() - start > BUDGET:
        return
    res = {"source": None, "directors": [], "at": now()}
    sources = (("Credonex", enrich.directors_credonex), ("Falcon Ebiz", enrich.directors_falconebiz),
               ("directors API", lambda cin, name: enrich.directors_api(cin)))   # only if DIRECTORS_API_URL is set
    for label, fn in sources:
        try:
            found = fn(r["c"], r["n"])
        except Exception:  # noqa: BLE001
            continue
        if found:
            res = {"source": label, "directors": found, "at": now()}; break
    with lock:
        cache[r["c"]] = res; done[0] += 1
        if res["directors"]:
            done[1] += 1
            r["f"] = ", ".join(x["name"] for x in res["directors"])
            r["din"] = ", ".join(x.get("din") or "" for x in res["directors"])
        if time.time() - last_pub[0] > 600:   # checkpoint every 10 minutes
            publish(); last_pub[0] = time.time()
            print(f"  {done[0]} looked up, {done[1]} found", flush=True)


with ThreadPoolExecutor(6) as ex:
    list(ex.map(work, todo))
publish()
print(f"done: {added} new companies, {done[0]} founder lookups, {done[1]} found, {time.time() - start:.0f}s", flush=True)
