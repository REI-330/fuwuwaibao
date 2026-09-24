"""Bounded official-file collector. Run: python -m acquisition.collect --source ID.

An acquired file is evidence awaiting extraction/review, not a validated fact.
No recursive crawling, private interfaces, or automatic promotion of reports.
"""
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).with_name("sources.json")
MAX_BYTES = 150 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def collect(source, proxy=None, data_dir=None):
    if source["mode"] != "public_document":
        raise ValueError("Source requires discovery/authorization; automated acquisition disabled")
    base = Path(data_dir) if data_dir else ROOT / "data"
    directory = base / "sources" / source["id"]
    directory.mkdir(parents=True, exist_ok=True)
    attempt = {"source_id": source["id"], "attempted_at": now(), "state": "DEGRADED"}
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = "CareerKnowledgeResearch/0.1 (official-document acquisition)"
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    started = time.monotonic()
    try:
        with session.get(source["source_url"], timeout=(12, 40), stream=True) as response:
            response.raise_for_status()
            final = urlparse(response.url)
            original = urlparse(source["source_url"])
            same_host = final.hostname and original.hostname and final.hostname.lower().lstrip("www.") == original.hostname.lower().lstrip("www.")
            if final.scheme != "https" and not (same_host and source.get("allow_same_host_http_redirect", False)):
                raise ValueError("HTTPS downgrade rejected")
            blob = bytearray()
            for chunk in response.iter_content(65536):
                blob.extend(chunk)
                if len(blob) > MAX_BYTES or time.monotonic() - started > 120:
                    raise ValueError("Document exceeds configured size/time limit")
            if source["format"] == "pdf" and not blob.startswith(b"%PDF-"):
                raise ValueError("Expected PDF; received HTML/error/challenge")
            if len(blob) < 100:
                raise ValueError("Empty or unexpectedly small document")
            digest = hashlib.sha256(blob).hexdigest()
            snapshot = directory / (digest + "." + source["format"])
            if not snapshot.exists():
                temporary = snapshot.with_suffix(snapshot.suffix + ".part")
                temporary.write_bytes(blob)
                os.replace(temporary, snapshot)
            previous = json.loads((directory / "latest.json").read_text(encoding="utf-8")) if (directory / "latest.json").exists() else None
            metadata = dict(source, original_title=source["source_name"], fetched_at=now(),
                            last_verified_at=now(), acquisition_method="https_public_document",
                            raw_content_hash=digest, snapshot=str(snapshot.relative_to(base)),
                            resolved_url=response.url, transport_warning=("same_host_http_redirect" if final.scheme != "https" else None), content_type=response.headers.get("Content-Type"),
                            bytes=len(blob), review_status="PENDING", redistribution_allowed=False)
            save_json(directory / (digest + ".metadata.json"), metadata)
            save_json(directory / "latest.json", metadata)
            attempt.update(state="ACTIVE", bytes=len(blob), raw_content_hash=digest,
                           changed=not previous or previous["raw_content_hash"] != digest)
    except (requests.RequestException, OSError, ValueError) as error:
        attempt.update(error=f"{type(error).__name__}: {error}")
    finally:
        session.close()
        attempt["duration_seconds"] = round(time.monotonic() - started, 3)
        save_json(directory / "health.json", attempt)
        with (directory / "attempts.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(attempt, ensure_ascii=False) + "\n")
    return attempt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--proxy", help="Explicit proxy, e.g. http://127.0.0.1:7897")
    args = parser.parse_args()
    sources = json.loads(REGISTRY.read_text(encoding="utf-8"))
    matches = [s for s in sources if s["id"] == args.source]
    if not matches:
        parser.error("Unknown source ID")
    result = collect(matches[0], args.proxy)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["state"] == "ACTIVE" else 1)


if __name__ == "__main__":
    main()
