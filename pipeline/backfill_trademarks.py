#!/usr/bin/env python3
"""One-off: read past Trade Marks Journals and save the kept filings to storage (tm-backfill/<journal>.json).
The nightly update merges those files into the register. Run by .github/workflows/backfill-trademarks.yml.

  python pipeline/backfill_trademarks.py --months 18 --shard 0 --shards 4
"""
import argparse, json, os, sys
from datetime import datetime, timedelta
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trademarks  # noqa: E402
from store import Store  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--months", type=int, default=3)
ap.add_argument("--shard", type=int, default=0)
ap.add_argument("--shards", type=int, default=1)
a = ap.parse_args()

st = Store()
state = st.get_json("trademarks.json", {})
have = {n.split(".")[0] for p in ("tm-backfill/", "tm-merged/") for n in st.list(p)}
skip = set(state.get("done", [])) | {int(x) for x in have if x.isdigit()}
journals = trademarks.latest_journals()   # newest first; one a week, so 4.35 per month
todo = [(jn, f) for jn, f in journals[: int(a.months * 4.35) + 1] if jn not in skip]
mine = [x for i, x in enumerate(todo) if i % a.shards == a.shard]
print(f"{len(todo)} journals to read across {a.shards} shards; this shard: {[jn for jn, _ in mine]}", flush=True)
for jn, files in mine:
    filings = trademarks.process_journal(jn, files, log=lambda m: print(m, flush=True))
    st.put(f"tm-backfill/{jn}.json", json.dumps({"jn": jn, "filings": filings}))
    print(f"journal {jn}: saved {len(filings)} filings", flush=True)
