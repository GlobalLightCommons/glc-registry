import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_cfg(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_json(url: str, timeout=20):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "glc-registry-bot"})
    r.raise_for_status()
    return r.json()


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "datasets.yml"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("registry_out")
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_cfg(cfg_path)
    datasets = (cfg.get("datasets") or [])
    results = []
    errors = []

    for ds in datasets:
        ds_id = ds.get("id") or ds.get("repo") or "unknown"
        url = ds.get("validation_url")
        if not url:
            errors.append({"id": ds_id, "error": "missing validation_url"})
            continue

        try:
            data = fetch_json(url)
            # Normalize a little
            results.append({
                "id": ds_id,
                "repo": ds.get("repo"),
                "validation_url": url,
                "fetched_at_utc": utc_now(),
                "validation": data,
            })
        except Exception as e:
            errors.append({
                "id": ds_id,
                "repo": ds.get("repo"),
                "validation_url": url,
                "fetched_at_utc": utc_now(),
                "error": str(e),
            })

        time.sleep(0.2)

    payload = {
        "generated_at_utc": utc_now(),
        "dataset_count": len(datasets),
        "fetched_count": len(results),
        "error_count": len(errors),
        "datasets": results,
        "fetch_errors": errors,
    }

    (out_dir / "registry.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    # Simple status file for humans
    (out_dir / "README.txt").write_text(
        f"Generated at {payload['generated_at_utc']}\n"
        f"Fetched: {payload['fetched_count']}/{payload['dataset_count']}\n"
        f"Errors: {payload['error_count']}\n",
        encoding="utf-8",
    )

    # Fail the action only if EVERYTHING failed to fetch
    if len(results) == 0 and len(datasets) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
