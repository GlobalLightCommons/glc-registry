#!/usr/bin/env python3
"""
validate_datasets_pr.py

PR guardrail for glc-registry:
- Validates datasets.yml structure
- Enforces uniqueness of id and repo
- Ensures required fields exist
- Verifies that latest_pass_url is reachable and returns JSON with status == "pass"
- Optionally verifies current_url is reachable (but does NOT require pass)

Usage:
  python scripts/validate_datasets_pr.py datasets.yml
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Tuple

import requests
import yaml


UA = "glc-registry-pr-validator"
TIMEOUT_S = 20


def fetch_json(url: str) -> Dict[str, Any]:
    """Fetch URL and parse JSON. Raises requests/JSON errors with context."""
    r = requests.get(url, timeout=TIMEOUT_S, headers={"User-Agent": UA})
    r.raise_for_status()
    try:
        return r.json()
    except json.JSONDecodeError as e:
        raise ValueError(f"URL did not return valid JSON: {url} ({e})") from e


def load_datasets(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    datasets = cfg.get("datasets")
    if not isinstance(datasets, list):
        raise ValueError("datasets.yml must contain a top-level key `datasets:` that is a list")
    # Ensure each item is a dict
    for i, ds in enumerate(datasets):
        if not isinstance(ds, dict):
            raise ValueError(f"datasets[{i}] must be a mapping/object, got: {type(ds).__name__}")
    return datasets


def require_fields(ds: Dict[str, Any], fields: List[str]) -> None:
    missing = [k for k in fields if not ds.get(k)]
    if missing:
        ds_id = ds.get("id") or ds.get("repo") or "unknown"
        raise ValueError(f"{ds_id}: missing required field(s): {', '.join(missing)}")


def enforce_uniqueness(datasets: List[Dict[str, Any]]) -> None:
    ids = set()
    repos = set()

    for ds in datasets:
        ds_id = ds.get("id")
        repo = ds.get("repo")

        if ds_id in ids:
            raise ValueError(f"Duplicate dataset id: {ds_id}")
        ids.add(ds_id)

        if repo in repos:
            raise ValueError(f"Duplicate repo: {repo}")
        repos.add(repo)


def validate_one(ds: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Returns: (ok, messages)
    ok means "registry-compliant": latest_pass_url fetchable and status == pass
    """
    msgs: List[str] = []
    ds_id = ds.get("id") or ds.get("repo") or "unknown"

    require_fields(ds, ["id", "repo", "latest_pass_url", "current_url"])

    latest_url = ds["latest_pass_url"]
    current_url = ds["current_url"]

    # latest_pass MUST exist and be pass
    msgs.append(f"Checking {ds_id} latest_pass...")
    latest = fetch_json(latest_url)

    status = (latest.get("status") or "").lower()
    if status != "pass":
        raise ValueError(f"{ds_id}: latest_pass.status must be 'pass' (got {latest.get('status')})")

    # current is OPTIONAL: we only check that it is fetchable JSON
    msgs.append(f"Checking {ds_id} current...")
    _ = fetch_json(current_url)

    return True, msgs


def main(cfg_path: str) -> int:
    datasets = load_datasets(cfg_path)

    # Structural safety
    enforce_uniqueness(datasets)

    # Validate each dataset entry
    ok_count = 0
    failures: List[str] = []

    for ds in datasets:
        ds_id = ds.get("id") or ds.get("repo") or "unknown"
        try:
            ok, msgs = validate_one(ds)
            for m in msgs:
                print(m)
            if ok:
                ok_count += 1
        except Exception as e:
            failures.append(f"{ds_id}: {e}")

    print(f"\nSummary: {ok_count}/{len(datasets)} entries registry-compliant.")

    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  - {f}")
        return 1

    return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "datasets.yml"
    raise SystemExit(main(path))