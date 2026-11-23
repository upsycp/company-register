#!/usr/bin/env python3
"""One-time: copy the register you built on this Mac into the private Supabase bucket.
Run it from the project folder:  .venv/bin/python pipeline/seed.py
It asks for the project URL and service_role key; the key is never saved or printed."""
import getpass, json, os, sys
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from store import Store  # noqa: E402

# settings can come from a local .env file (git ignores it) instead of the prompts below
envf = os.path.join(ROOT, ".env")
if os.path.exists(envf):
    for line in open(envf):
        k, _, v = line.partition("=")
        if k.strip() in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") and v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
url = os.environ.get("SUPABASE_URL") or input("Supabase Project URL: ").strip()
key = os.environ.get("SUPABASE_SERVICE_KEY") or getpass.getpass("service_role key (hidden): ").strip()
st = Store(url, key)
data = json.load(open(os.path.join(ROOT, "site", "data.json")))
cache = json.load(open(os.path.join(ROOT, "report", "cache_directors_all.json")))
for v in cache.values():
    v.setdefault("at", "2026-11-23T00:00:00+00:00")
st.put("data.json", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
st.put("founders.json", json.dumps(cache))
st.put("meta.json", json.dumps({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "count": len(data),
                                "months": sorted({r["m"] for r in data if r.get("m")})}))
print(f"Uploaded {len(data):,} companies and {len(cache):,} founder lookups. Open the site and sign in.")
