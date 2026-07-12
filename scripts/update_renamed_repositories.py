import json
import sys
from pathlib import Path

import yaml


def main():
    cfg_path = Path(sys.argv[1] if len(sys.argv) > 1 else "datasets.yml")
    registry_path = Path(
        sys.argv[2] if len(sys.argv) > 2 else "registry_out/registry.json"
    )

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    renames = {
        item["configured_repo"]: item["repo"]
        for item in registry.get("datasets", [])
        if item.get("repository_status") == "renamed"
        and item.get("configured_repo")
        and item.get("repo")
    }

    changed = []
    for dataset in cfg.get("datasets", []):
        configured_repo = dataset.get("repo")
        canonical_repo = renames.get(configured_repo)
        if canonical_repo and canonical_repo != configured_repo:
            dataset["repo"] = canonical_repo
            changed.append((configured_repo, canonical_repo))

    if not changed:
        print("No renamed repositories need updating.")
        return 0

    cfg_path.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    for configured_repo, canonical_repo in changed:
        print(f"Updated {configured_repo} -> {canonical_repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
