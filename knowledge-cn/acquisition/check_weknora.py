"""Runtime gate for the real WeKnora instance (no mocks, no credentials stored)."""
import json
import os
import urllib.request
import urllib.error

base = os.environ.get("WEKNORA_URL", "http://127.0.0.1:8080").rstrip("/")
token = os.environ.get("WEKNORA_TOKEN", "")
def get(path):
    req = urllib.request.Request(base + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.load(r)

try:
    status, health = get("/health")
except urllib.error.HTTPError as exc:
    raise SystemExit(f"health gate HTTP error: {exc.code}; check WEKNORA_URL")
if status != 200 or health.get("status") != "ok":
    raise SystemExit(f"health gate failed: {status} {health}")
try:
    status, models = get("/api/v1/models")
except urllib.error.HTTPError as exc:
    if exc.code == 401:
        raise SystemExit("authentication gate failed: set WEKNORA_TOKEN from a real login")
    raise SystemExit(f"model gate HTTP error: {exc.code}")
items = models.get("data", []) if isinstance(models, dict) else []
print(json.dumps({"health": health, "model_count": len(items), "models": items}, ensure_ascii=False))
if not items:
    raise SystemExit("no embedding/model configured; import and retrieval gate remains blocked")
