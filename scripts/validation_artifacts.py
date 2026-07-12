import json
import os
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests


UA = {"User-Agent": "glc-registry-bot"}
TRUSTED_SIGNER_REPO = os.getenv("TRUSTED_VALIDATOR_REPO", "tscnlab/glee-metadata-validator")
TRUSTED_SIGNER_WORKFLOW = os.getenv(
    "TRUSTED_VALIDATOR_WORKFLOW",
    "tscnlab/glee-metadata-validator/.github/workflows/validate.yml",
)
TRUSTED_SIGNER_DIGEST = os.getenv("TRUSTED_VALIDATOR_WORKFLOW_DIGEST")
ALLOWED_VALIDATOR_IMAGE_PREFIX = os.getenv(
    "ALLOWED_VALIDATOR_IMAGE_PREFIX",
    "ghcr.io/tscnlab/glee-validator@sha256:",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def repo_name_from_slug(repo):
    if isinstance(repo, str) and "/" in repo:
        return repo.split("/", 1)[1]
    return repo or "unknown"


def github_headers():
    headers = dict(UA)
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return headers


def github_get_json(url, timeout=30):
    r = requests.get(url, timeout=timeout, headers=github_headers())
    r.raise_for_status()
    return r.json()


def resolve_repository(repo):
    """Return the canonical owner/name, following GitHub rename redirects."""
    data = github_get_json(f"https://api.github.com/repos/{repo}")
    return data["full_name"]


def github_download(url, dest_path, timeout=60):
    with requests.get(url, timeout=timeout, headers=github_headers(), stream=True) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def get_commit_sha(repo, branch_or_sha):
    if len(branch_or_sha) == 40 and all(c in "0123456789abcdefABCDEF" for c in branch_or_sha):
        return branch_or_sha
    data = github_get_json(f"https://api.github.com/repos/{repo}/commits/{branch_or_sha}")
    return data["sha"]


def list_runs_for_sha(repo, sha):
    url = f"https://api.github.com/repos/{repo}/actions/runs?head_sha={sha}&status=completed&per_page=50"
    return github_get_json(url).get("workflow_runs", [])


def list_artifacts_for_run(repo, run_id):
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"
    return github_get_json(url).get("artifacts", [])


def find_validation_artifact(repo, sha, require_success=False):
    runs = sorted(
        list_runs_for_sha(repo, sha),
        key=lambda r: r.get("created_at") or "",
        reverse=True,
    )
    for run in runs:
        if require_success and run.get("conclusion") != "success":
            continue
        artifacts = list_artifacts_for_run(repo, run["id"])
        for artifact in artifacts:
            if artifact.get("name") != "validation-report" or artifact.get("expired"):
                continue
            return {"run": run, "artifact": artifact}
    return None


def download_validation_artifact(repo, artifact, dest_dir):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "validation-report.zip"
    github_download(
        f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact['id']}/zip",
        zip_path,
    )
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    return dest_dir


def find_report_file(extract_dir, filename):
    extract_dir = Path(extract_dir)
    candidates = [
        extract_dir / "validation_out" / filename,
        extract_dir / filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = list(extract_dir.rglob(filename))
    return matches[0] if matches else None


def verify_attestation(path, repo, expected_sha):
    if shutil.which("gh") is None:
        raise RuntimeError("GitHub CLI 'gh' is required to verify artifact attestations")

    cmd = [
        "gh",
        "attestation",
        "verify",
        str(path),
        "--repo",
        repo,
        "--signer-workflow",
        TRUSTED_SIGNER_WORKFLOW,
        "--source-digest",
        expected_sha,
        "--deny-self-hosted-runners",
        "--format",
        "json",
    ]
    if TRUSTED_SIGNER_DIGEST:
        cmd.extend(["--signer-digest", TRUSTED_SIGNER_DIGEST])
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "attestation verification failed")
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return {"raw": result.stdout}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_report_content(report, repo, expected_sha):
    errors = []
    if report.get("repo") != repo:
        errors.append(f"report repo {report.get('repo')!r} does not match {repo!r}")
    if report.get("commit_sha") != expected_sha:
        errors.append(f"report commit_sha {report.get('commit_sha')!r} does not match {expected_sha!r}")
    if report.get("status") not in {"pass", "fail"}:
        errors.append(f"report status {report.get('status')!r} is not pass/fail")
    image = report.get("validator_image") or ""
    if ALLOWED_VALIDATOR_IMAGE_PREFIX and not image.startswith(ALLOWED_VALIDATOR_IMAGE_PREFIX):
        errors.append(f"validator_image {image!r} does not start with {ALLOWED_VALIDATOR_IMAGE_PREFIX!r}")
    return errors


def verify_validation_artifact(repo, expected_sha, artifact_info, work_dir):
    if not artifact_info:
        raise RuntimeError(f"No validation-report artifact found for {repo}@{expected_sha}")

    run = artifact_info["run"]
    artifact = artifact_info["artifact"]
    extract_dir = download_validation_artifact(repo, artifact, work_dir)

    validation_path = find_report_file(extract_dir, "validation.json")
    manifest_path = find_report_file(extract_dir, "validated-files-manifest.json")
    if not validation_path:
        raise RuntimeError("validation-report artifact is missing validation.json")
    if not manifest_path:
        raise RuntimeError("validation-report artifact is missing validated-files-manifest.json")

    validation_attestation = verify_attestation(validation_path, repo, expected_sha)
    manifest_attestation = verify_attestation(manifest_path, repo, expected_sha)

    report = load_json(validation_path)
    manifest = load_json(manifest_path)
    content_errors = validate_report_content(report, repo, expected_sha)
    if manifest.get("repo") != repo:
        content_errors.append(f"manifest repo {manifest.get('repo')!r} does not match {repo!r}")
    if manifest.get("commit_sha") != expected_sha:
        content_errors.append(
            f"manifest commit_sha {manifest.get('commit_sha')!r} does not match {expected_sha!r}"
        )
    if content_errors:
        raise RuntimeError("; ".join(content_errors))

    return {
        "run_id": run.get("id"),
        "run_url": run.get("html_url"),
        "run_conclusion": run.get("conclusion"),
        "artifact_id": artifact.get("id"),
        "artifact_url": artifact.get("archive_download_url"),
        "validation": report,
        "manifest": manifest,
        "validation_attestation": validation_attestation,
        "manifest_attestation": manifest_attestation,
    }


def find_latest_verified_pass(repo, current_sha, current_result, work_root, max_runs=25):
    if current_result and current_result["validation"].get("status") == "pass":
        return current_result

    url = f"https://api.github.com/repos/{repo}/actions/runs?status=completed&per_page={max_runs}"
    runs = sorted(
        github_get_json(url).get("workflow_runs", []),
        key=lambda r: r.get("created_at") or "",
        reverse=True,
    )

    for run in runs:
        sha = run.get("head_sha")
        if not sha or sha == current_sha:
            continue
        artifacts = list_artifacts_for_run(repo, run["id"])
        for artifact in artifacts:
            if artifact.get("name") != "validation-report" or artifact.get("expired"):
                continue
            try:
                result = verify_validation_artifact(
                    repo,
                    sha,
                    {"run": run, "artifact": artifact},
                    Path(work_root) / "latest_pass_candidates" / repo.replace("/", "__") / sha,
                )
                if result["validation"].get("status") == "pass":
                    return result
            except Exception:
                continue
            time.sleep(0.2)
    return None
