"""Tiny client for the private Supabase storage bucket that holds the register. Server-side only (service key)."""
import json, os
from urllib.parse import quote

import requests

BUCKET = "register"


class Store:
    def __init__(self, url=None, key=None):
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/")
        key = key or os.environ["SUPABASE_SERVICE_KEY"]
        self.h = {"Authorization": f"Bearer {key}", "apikey": key}

    def _o(self, path):
        return f"{self.url}/storage/v1/object/{BUCKET}/{quote(path)}"

    def get(self, path):
        r = requests.get(self._o(path), headers=self.h, timeout=120)
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.content

    def get_json(self, path, default):
        b = self.get(path)
        return json.loads(b) if b else default

    def put(self, path, data, ctype="application/json"):
        if isinstance(data, str):
            data = data.encode()
        r = requests.post(self._o(path), headers={**self.h, "Content-Type": ctype, "x-upsert": "true",
                                                  "Cache-Control": "no-cache"}, data=data, timeout=300)
        r.raise_for_status()

    def list(self, prefix):
        r = requests.post(f"{self.url}/storage/v1/object/list/{BUCKET}", headers=self.h, timeout=60,
                          json={"prefix": prefix, "limit": 1000, "sortBy": {"column": "name", "order": "asc"}})
        r.raise_for_status()
        return [o["name"] for o in r.json() if o.get("id")]   # folders have no id

    def move(self, src, dst):
        r = requests.post(f"{self.url}/storage/v1/object/move", headers=self.h, timeout=60,
                          json={"bucketId": BUCKET, "sourceKey": src, "destinationKey": dst})
        r.raise_for_status()
