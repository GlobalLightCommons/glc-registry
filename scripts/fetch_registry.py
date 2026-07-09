import json
import sys
from pathlib import Path

import yaml

from validation_artifacts import (
    find_latest_verified_pass,
    find_validation_artifact,
    get_commit_sha,
    utc_now,
    verify_validation_artifact,
)


def load_cfg(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "datasets.yml"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("registry_out")
    work_dir = out_dir / "_validation_artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_cfg(cfg_path)
    datasets = cfg.get("datasets") or []

    results = []
    errors = []

    for ds in datasets:
        ds_id = ds.get("id") or ds.get("repo") or "unknown"
        repo = ds.get("repo")
        branch = ds.get("branch") or "main"
        requested_commit = ds.get("commit")

        item = {
            "id": ds_id,
            "repo": repo,
            "branch": branch,
            "requested_commit": requested_commit,
            "resolved_commit_sha": None,
            "fetched_at_utc": utc_now(),
            "current_status": "unknown",
            "current": None,
            "latest_pass": None,
            "attestation_verified": False,
            "fetch_errors": [],
        }

        try:
            if not repo:
                raise RuntimeError("missing repo")

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
                current_manifest = current_result["manifest"]

                item["current_status"] = current_report.get("status")
                item["attestation_verified"] = True
                item["current"] = {
                    "status": current_report.get("status"),
                    "repo": current_report.get("repo"),
                    "commit_sha": current_report.get("commit_sha"),
                    "validator_version": current_report.get("validator_version"),
                    "validator_image": current_report.get("validator_image"),
                    "timestamp_utc": current_report.get("timestamp_utc"),
                    "error_count": current_report.get("error_count"),
                    "warning_count": current_report.get("warning_count"),
                    "file_manifest": current_report.get("file_manifest"),
                    "manifest_file_count": len(current_manifest.get("files", [])),
                    "run_id": current_result.get("run_id"),
                    "run_url": current_result.get("run_url"),
                    "run_conclusion": current_result.get("run_conclusion"),
                    "artifact_id": current_result.get("artifact_id"),
                }
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
                latest_report = latest_result["validation"]
                latest_manifest = latest_result["manifest"]
                item["latest_pass"] = {
                    "status": latest_report.get("status"),
                    "repo": latest_report.get("repo"),
                    "commit_sha": latest_report.get("commit_sha"),
                    "validator_version": latest_report.get("validator_version"),
                    "validator_image": latest_report.get("validator_image"),
                    "timestamp_utc": latest_report.get("timestamp_utc"),
                    "error_count": latest_report.get("error_count"),
                    "warning_count": latest_report.get("warning_count"),
                    "file_manifest": latest_report.get("file_manifest"),
                    "manifest_file_count": len(latest_manifest.get("files", [])),
                    "run_id": latest_result.get("run_id"),
                    "run_url": latest_result.get("run_url"),
                    "run_conclusion": latest_result.get("run_conclusion"),
                    "artifact_id": latest_result.get("artifact_id"),
                }

        except Exception as e:
            item["fetch_errors"].append({"error": str(e)})
            errors.append(
                {
                    "id": ds_id,
                    "repo": repo,
                    "branch": branch,
                    "requested_commit": requested_commit,
                    "resolved_commit_sha": item.get("resolved_commit_sha"),
                    "fetched_at_utc": utc_now(),
                    "error": str(e),
                }
            )

        results.append(item)

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

    (out_dir / "README.txt").write_text(
        f"Generated at {payload['generated_at_utc']}\n"
        f"Fetched: {payload['fetched_count']}/{payload['dataset_count']}\n"
        f"Errors: {payload['error_count']}\n",
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
