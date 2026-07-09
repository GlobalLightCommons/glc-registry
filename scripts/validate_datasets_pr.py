import sys
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import yaml

from validation_artifacts import (
    find_validation_artifact,
    get_commit_sha,
    verify_validation_artifact,
)


def die(msg: str, code: int = 1):
    print(f"\n❌ {msg}")
    raise SystemExit(code)


def load_cfg(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_base_cfg(path: str):
    base_ref = os.getenv("GITHUB_BASE_REF")
    if not base_ref:
        return None

    candidates = [f"origin/{base_ref}", base_ref]
    for ref in candidates:
        try:
            result = subprocess.run(
                ["git", "show", f"{ref}:{path}"],
                text=True,
                capture_output=True,
                check=True,
            )
            return yaml.safe_load(result.stdout) or {}
        except subprocess.CalledProcessError:
            continue
    return None


def dataset_key(ds):
    if not isinstance(ds, dict):
        return None
    return ds.get("id") or ds.get("repo")


def changed_dataset_entries(current_datasets, base_cfg):
    if base_cfg is None:
        print("No PR base dataset file found; checking all dataset entries.")
        return current_datasets

    base_datasets = base_cfg.get("datasets") or []
    base_by_id = {
        dataset_key(ds): ds
        for ds in base_datasets
        if isinstance(ds, dict) and dataset_key(ds)
    }

    changed = []
    for ds in current_datasets:
        key = dataset_key(ds)
        if not key:
            continue
        if base_by_id.get(key) != ds:
            changed.append(ds)
    return changed


def is_repo_slug(value: str) -> bool:
    if not isinstance(value, str) or "/" not in value:
        return False
    owner, name = value.split("/", 1)
    return bool(owner) and bool(name) and " " not in value


def is_http_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def main(cfg_path: str):
    cfg = load_cfg(cfg_path)
    base_cfg = load_base_cfg(cfg_path)
    datasets = cfg.get("datasets") or []
    if not isinstance(datasets, list) or len(datasets) == 0:
        die("datasets.yml must contain a non-empty top-level 'datasets:' list")

    required_keys = ("id", "repo")

    seen_ids = set()
    seen_repos = set()
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

        if ds_id in seen_ids:
            errors.append(f"Duplicate id: {ds_id}")
        seen_ids.add(ds_id)

        if repo in seen_repos:
            errors.append(f"Duplicate repo: {repo}")
        seen_repos.add(repo)

        if not is_repo_slug(repo):
            errors.append(f"{ds_id}: repo must be an owner/name GitHub repo slug: {repo}")

        # Backward-compatible: old URL fields may remain for now, but must be URL-shaped.
        for url_key in ("latest_pass_url", "current_url"):
            if ds.get(url_key) and not is_http_url(ds[url_key]):
                errors.append(f"{ds_id}: {url_key} is not a valid http(s) URL: {ds[url_key]}")

    if errors:
        die("Registry validation failed:\n" + "\n".join(f" - {e}" for e in errors))

    verification_errors = []
    work_dir = Path("_registry_pr_validation")

    datasets_to_verify = changed_dataset_entries(datasets, base_cfg)
    if datasets_to_verify:
        changed_ids = ", ".join(ds.get("id") or ds.get("repo") for ds in datasets_to_verify)
        print(f"Trusted validation will check changed dataset entries only: {changed_ids}")
    else:
        print("No dataset entries were added or changed; skipping trusted artifact checks.")

    for ds in datasets_to_verify:
        ds_id = ds["id"]
        repo = ds["repo"]
        branch = ds.get("branch") or "main"
        requested_commit = ds.get("commit")

        print(f"Checking trusted validation artifact for {ds_id} ({repo})...")
        try:
            expected_sha = get_commit_sha(repo, requested_commit or branch)
            artifact_info = find_validation_artifact(repo, expected_sha)
            result = verify_validation_artifact(
                repo,
                expected_sha,
                artifact_info,
                work_dir / repo.replace("/", "__") / expected_sha,
            )
            status = result["validation"].get("status")
            if status != "pass":
                raise RuntimeError(f"current validation status is {status!r}, expected 'pass'")
        except Exception as e:
            verification_errors.append(f"{ds_id}: trusted validation check failed: {e}")

    if verification_errors:
        die("Trusted validation checks failed:\n" + "\n".join(f" - {e}" for e in verification_errors))

    print("\n✅ datasets.yml entries have trusted passing validation artifacts.")


if __name__ == "__main__":
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "datasets.yml"
    main(cfg_path)
