"""Tiny client for the private Supabase storage bucket that holds the register. Server-side only (service key)."""
import json, os, re
from urllib.parse import quote

import requests

BUCKET = "register"


def _check(r):
    if r.status_code >= 400:
        raise SystemExit(f"Supabase said {r.status_code}: {r.text[:300]}\n"
                         "Bucket not found → run supabase/schema.sql in the SQL Editor first.\n"
                         "Invalid key / JWS / signature → use the service_role (or sb_secret_) key, not the anon key.")


class Store:
    def __init__(self, url=None, key=None):
        url = (url or os.environ["SUPABASE_URL"]).strip()
        m = re.match(r"(https?://[^/\s]+)", url)   # accept the API URL too (…/rest/v1/)
        self.url = m.group(1) if m else url.rstrip("/")
        key = key or os.environ["SUPABASE_SERVICE_KEY"]
        key = key.strip()
        # new-style secret keys (sb_secret_…) go in the apikey header only; legacy JWT keys also as Bearer
        self.h = {"apikey": key, **({"Authorization": f"Bearer {key}"} if key.startswith("ey") else {})}

    def _o(self, path):
        return f"{self.url}/storage/v1/object/{BUCKET}/{quote(path)}"

    def get(self, path):
        r = requests.get(self._o(path), headers=self.h, timeout=120)
        if r.status_code in (400, 404):
            return None
        _check(r)
        return r.content

    def get_json(self, path, default):
        b = self.get(path)
        return json.loads(b) if b else default

    def put(self, path, data, ctype="application/json"):
        if isinstance(data, str):
            data = data.encode()
        r = requests.post(self._o(path), headers={**self.h, "Content-Type": ctype, "x-upsert": "true",
                                                  "Cache-Control": "no-cache"}, data=data, timeout=300)
        _check(r)

    def list(self, prefix):
        r = requests.post(f"{self.url}/storage/v1/object/list/{BUCKET}", headers=self.h, timeout=60,
                          json={"prefix": prefix, "limit": 1000, "sortBy": {"column": "name", "order": "asc"}})
        _check(r)
        return [o["name"] for o in r.json() if o.get("id")]   # folders have no id

    def move(self, src, dst):
        r = requests.post(f"{self.url}/storage/v1/object/move", headers=self.h, timeout=60,
                          json={"bucketId": BUCKET, "sourceKey": src, "destinationKey": dst})
        _check(r)
