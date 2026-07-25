import json
import os
import re
import sys
from pathlib import Path

import yaml

from validation_artifacts import (
    find_latest_verified_pass,
    find_validation_artifact,
    get_commit_sha,
    repo_name_from_slug,
    resolve_repository,
    utc_now,
    verify_validation_artifact,
)

CURRENT_GLC_SCHEMA_VERSION = os.getenv("CURRENT_GLC_SCHEMA_VERSION", "3.0.0")


def load_cfg(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def version_key(version):
    if not isinstance(version, str):
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def schema_lifecycle(schema_version, current_version=CURRENT_GLC_SCHEMA_VERSION):
    schema_key = version_key(schema_version)
    current_key = version_key(current_version)
    if schema_key is None or current_key is None:
        return "unrecognized"
    if schema_key == current_key:
        return "current"
    if schema_key < current_key:
        return "legacy"
    return "unrecognized"


def validation_summary(result, current_schema_version=CURRENT_GLC_SCHEMA_VERSION):
    report = result["validation"]
    manifest = result["manifest"]
    schema_version = report.get("schema_version")
    lifecycle = schema_lifecycle(schema_version, current_schema_version)
    return {
        "status": report.get("status"),
        "repo": report.get("repo"),
        "commit_sha": report.get("commit_sha"),
        "schema_version": schema_version,
        "schema_lifecycle": lifecycle,
        "upgrade_recommended": lifecycle == "legacy",
        "validator_version": report.get("validator_version"),
        "validator_image": report.get("validator_image"),
        "validator_trust_policy": result.get("trust_policy"),
        "timestamp_utc": report.get("timestamp_utc"),
        "error_count": report.get("error_count"),
        "warning_count": report.get("warning_count"),
        "file_manifest": report.get("file_manifest"),
        "manifest_file_count": len(manifest.get("files", [])),
        "run_id": result.get("run_id"),
        "run_url": result.get("run_url"),
        "run_conclusion": result.get("run_conclusion"),
        "artifact_id": result.get("artifact_id"),
    }


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "datasets.yml"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("registry_out")
    work_dir = out_dir / "_validation_artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_cfg(cfg_path)
    current_schema_version = cfg.get(
        "current_schema_version", CURRENT_GLC_SCHEMA_VERSION
    )
    datasets = cfg.get("datasets") or []

    results = []
    errors = []

    for ds in datasets:
        item = {
            "id": "unknown",
            "repo": None,
            "configured_repo": None,
            "repository_status": "unknown",
            "branch": "main",
            "requested_commit": None,
            "resolved_commit_sha": None,
            "fetched_at_utc": utc_now(),
            "current_status": "unknown",
            "current": None,
            "latest_pass": None,
            "attestation_verified": False,
            "fetch_errors": [],
        }

        try:
            if not isinstance(ds, dict):
                raise RuntimeError(
                    f"malformed dataset entry: expected a mapping, got {type(ds).__name__}"
                )

            repo = ds.get("repo")
            branch = ds.get("branch") or "main"
            requested_commit = ds.get("commit")

            if not isinstance(repo, str) or repo.count("/") != 1:
                raise RuntimeError(
                    f"invalid repo slug {repo!r}: expected 'owner/name'"
                )

            ds_id = repo_name_from_slug(repo)
            item.update(
                {
                    "id": ds_id,
                    "repo": repo,
                    "configured_repo": repo,
                    "branch": branch,
                    "requested_commit": requested_commit,
                }
            )

            canonical_repo = resolve_repository(repo)
            item["repo"] = canonical_repo
            item["repository_status"] = (
                "active" if canonical_repo.casefold() == repo.casefold() else "renamed"
            )
            repo = canonical_repo

            expected_sha = get_commit_sha(repo, requested_commit or branch)
            item["resolved_commit_sha"] = expected_sha

            artifact_info = find_validation_artifact(repo, expected_sha)
            current_result = None
            if artifact_info:
                current_result = verify_validation_artifact(
                    repo,
                    expected_sha,
                    artifact_info,
                    work_dir / repo.replace("/", "__") / expected_sha,
                )
                current_report = current_result["validation"]

                item["current_status"] = current_report.get("status")
                item["attestation_verified"] = True
                item["current"] = validation_summary(
                    current_result, current_schema_version
                )
            else:
                item["current_status"] = "missing_artifact"
                item["fetch_errors"].append(
                    {"error": f"No validation-report artifact found for {repo}@{expected_sha}"}
                )

            latest_result = find_latest_verified_pass(
                repo,
                expected_sha,
                current_result,
                work_dir / repo.replace("/", "__"),
            )
            if latest_result:
                item["latest_pass"] = validation_summary(
                    latest_result, current_schema_version
                )

        except Exception as e:
            response = getattr(e, "response", None)
            if (
                item["repository_status"] == "unknown"
                and response is not None
                and response.status_code == 404
            ):
                item["repository_status"] = "unavailable"
            item["fetch_errors"].append({"error": str(e)})
            errors.append(
                {
                    "id": item["id"],
                    "repo": item["repo"],
                    "configured_repo": item["configured_repo"],
                    "repository_status": item["repository_status"],
                    "branch": item["branch"],
                    "requested_commit": item["requested_commit"],
                    "resolved_commit_sha": item.get("resolved_commit_sha"),
                    "fetched_at_utc": utc_now(),
                    "error": str(e),
                }
            )

        results.append(item)

    payload = {
        "generated_at_utc": utc_now(),
        "current_schema_version": current_schema_version,
        "dataset_count": len(datasets),
        "fetched_count": len(results),
        "error_count": len(errors),
        "datasets": results,
        "fetch_errors": errors,
    }

    (out_dir / "registry.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    (out_dir / "README.txt").write_text(
        f"Generated at {payload['generated_at_utc']}\n"
        f"Fetched: {payload['fetched_count']}/{payload['dataset_count']}\n"
        f"Errors: {payload['error_count']}\n",
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
