import json
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
    datasets = cfg.get("datasets") or []

    results = []
    errors = []

    for ds in datasets:
        ds_id = ds.get("id") or ds.get("repo") or "unknown"
        repo = ds.get("repo")
        latest_url = ds.get("latest_pass_url")
        current_url = ds.get("current_url")

        if not latest_url:
            errors.append({"id": ds_id, "repo": repo, "error": "missing latest_pass_url"})
            continue
        if not current_url:
            errors.append({"id": ds_id, "repo": repo, "error": "missing current_url"})
            continue

        item = {
            "id": ds_id,
            "repo": repo,
            "latest_pass_url": latest_url,
            "current_url": current_url,
            "fetched_at_utc": utc_now(),
            "latest_pass": None,
            "current": None,
            "fetch_errors": [],
        }

        # 1) latest_pass is REQUIRED for registry membership
        try:
            item["latest_pass"] = fetch_json(latest_url)
        except Exception as e:
            msg = f"latest_pass fetch failed: {e}"
            item["fetch_errors"].append({"type": "latest_pass", "url": latest_url, "error": str(e)})

            errors.append(
                {
                    "id": ds_id,
                    "repo": repo,
                    "latest_pass_url": latest_url,
                    "current_url": current_url,
                    "fetched_at_utc": utc_now(),
                    "error": msg,
                }
            )
            results.append(item)
            time.sleep(0.2)
            continue

        # 2) current is OPTIONAL (does not affect membership)
        try:
            item["current"] = fetch_json(current_url)
        except Exception as e:
            item["fetch_errors"].append({"type": "current", "url": current_url, "error": str(e)})

        results.append(item)
        time.sleep(0.2)

    payload = {
        "generated_at_utc": utc_now(),
        "dataset_count": len(datasets),
        "fetched_count": len(results),
        "error_count": len(errors),
        "datasets": results,
        "fetch_errors": errors,
    }

    (out_dir / "registry.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Optional human-readable status file
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