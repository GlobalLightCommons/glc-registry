import json
import sys
from urllib.parse import urlparse

import requests
import yaml


TIMEOUT = 20
UA = {"User-Agent": "glc-registry-pr-validator"}


def die(msg: str, code: int = 1):
    print(f"\n❌ {msg}")
    raise SystemExit(code)


def load_cfg(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def is_http_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def fetch_json(url: str):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=UA)
        r.raise_for_status()
    except requests.exceptions.SSLError as e:
        raise RuntimeError(f"SSL error fetching {url}: {e}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"HTTP error fetching {url}: {e}")

    # Ensure it's JSON
    try:
        return r.json()
    except json.JSONDecodeError:
        snippet = (r.text or "")[:200].replace("\n", " ")
        raise RuntimeError(f"Non-JSON response from {url}. First 200 chars: {snippet!r}")


def main(cfg_path: str):
    cfg = load_cfg(cfg_path)
    datasets = cfg.get("datasets") or []
    if not isinstance(datasets, list) or len(datasets) == 0:
        die("datasets.yml must contain a non-empty top-level 'datasets:' list")

    required_keys = ("id", "repo", "latest_pass_url", "current_url")

    # Duplicate guards
    seen_ids = set()
    seen_repos = set()
    seen_lp = set()
    seen_cur = set()

    errors = []

    for ds in datasets:
        if not isinstance(ds, dict):
            errors.append("Each entry under datasets: must be an object/map")
            continue

        missing = [k for k in required_keys if not ds.get(k)]
        if missing:
            errors.append(f"{ds.get('id') or ds.get('repo') or '<unknown>'}: missing {missing}")
            continue

        ds_id = ds["id"]
        repo = ds["repo"]
        latest = ds["latest_pass_url"]
        current = ds["current_url"]

        # Validate url shapes
        if not is_http_url(latest):
            errors.append(f"{ds_id}: latest_pass_url is not a valid http(s) URL: {latest}")
            continue
        if not is_http_url(current):
            errors.append(f"{ds_id}: current_url is not a valid http(s) URL: {current}")
            continue

        # Duplicates
        if ds_id in seen_ids:
            errors.append(f"Duplicate id: {ds_id}")
        seen_ids.add(ds_id)

        if repo in seen_repos:
            errors.append(f"Duplicate repo: {repo}")
        seen_repos.add(repo)

        if latest in seen_lp:
            errors.append(f"Duplicate latest_pass_url: {latest}")
        seen_lp.add(latest)

        if current in seen_cur:
            errors.append(f"Duplicate current_url: {current}")
        seen_cur.add(current)

    if errors:
        die("Registry validation failed:\n" + "\n".join(f" - {e}" for e in errors))

    # Network validation (last step so duplicates/missing keys fail fast)
    net_errors = []

    for ds in datasets:
        ds_id = ds["id"]
        latest = ds["latest_pass_url"]
        current = ds["current_url"]

        print(f"Checking {ds_id}...")
        # latest_pass must be fetchable + pass-shaped JSON
        try:
            lp = fetch_json(latest)
            if "status" not in lp:
                raise RuntimeError("latest_pass.json missing required key 'status'")
            if str(lp.get("status")).lower() != "pass":
                raise RuntimeError(f"latest_pass.json status is not 'pass' (got {lp.get('status')!r})")
        except Exception as e:
            net_errors.append(f"{ds_id}: latest_pass_url invalid/unreachable: {e}")
            # if latest pass is bad, no need to check current
            continue

        # current_url is allowed to be fail, but must be fetchable JSON
        try:
            cur = fetch_json(current)
            if "status" not in cur:
                raise RuntimeError("validation.json missing required key 'status'")
        except Exception as e:
            net_errors.append(f"{ds_id}: current_url invalid/unreachable: {e}")

    if net_errors:
        die("Network validation failed:\n" + "\n".join(f" - {e}" for e in net_errors))

    print("\n✅ datasets.yml looks good.")


if __name__ == "__main__":
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "datasets.yml"
    main(cfg_path)