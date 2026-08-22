from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_json(path: Path, *, required: bool = True) -> dict:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def sync(url: str, token: str, contract: dict, saved: dict) -> dict:
    payload = json.dumps({"contract": contract, "saved": saved}).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/import",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "VehicleWatchSync/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Vehicle dashboard import failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Vehicle dashboard import endpoint is unavailable.") from exc
    if not result.get("ok"):
        raise RuntimeError("Vehicle dashboard rejected the import.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync vehicle-watch state into cars.johnsons.casa")
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("/opt/data/.hermes/vehicle-watches/enclave-traverse-awd-clean-under-15000-48326.json"),
    )
    parser.add_argument(
        "--saved",
        type=Path,
        default=Path("/opt/data/cache/vehicle-search/saved-vehicle-final.json"),
    )
    parser.add_argument("--env-file", type=Path, default=Path("/opt/data/secrets/cars-app.env"))
    parser.add_argument("--url")
    args = parser.parse_args()

    env = {**read_env(args.env_file), **os.environ}
    url = args.url or env.get("CARS_APP_URL", "https://cars.johnsons.casa")
    token = env.get("CARS_INGEST_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("CARS_INGEST_TOKEN is not configured.")

    result = sync(url, token, load_json(args.contract), load_json(args.saved, required=False))
    print(
        "vehicle_dashboard_sync=ok "
        f"vehicles={result['vehicle_count']} collections={result['collection_count']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"vehicle_dashboard_sync=failed error={exc}", file=sys.stderr)
        raise SystemExit(1)
