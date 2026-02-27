import sys
import yaml
import requests


def fetch_json(url: str):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def main(path: str):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    datasets = cfg.get("datasets") or []

    if not datasets:
        raise SystemExit("No datasets found")

    for ds in datasets:
        ds_id = ds.get("id")
        latest = ds.get("latest_pass_url")
        current = ds.get("current_url")

        if not ds_id:
            raise SystemExit("Dataset missing id")

        if not latest:
            raise SystemExit(f"{ds_id}: missing latest_pass_url")

        if not current:
            raise SystemExit(f"{ds_id}: missing current_url")

        print(f"Checking {ds_id}...")

        lp = fetch_json(latest)

        if lp.get("status") != "pass":
            raise SystemExit(f"{ds_id}: latest_pass is not PASS")

    print("All datasets valid ✔")


if __name__ == "__main__":
    main(sys.argv[1])