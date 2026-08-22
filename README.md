# Vehicle Watch

Private, notes-enabled dashboard for Brett's daily Buick Enclave / Chevrolet Traverse search.

## Features

- Password-protected dashboard at `cars.johnsons.casa`
- Daily exact-match and saved-comparison collections
- Persistent notes and workflow status stored separately from imported listing data
- VIN/listing-key deduplication
- Listing freshness labels and live-source links
- Token-protected JSON import endpoint
- SQLite WAL persistence on a retained Kubernetes PVC
- Timestamped SQLite backups after every note or password change
- CSRF protection, signed secure sessions, login throttling, strict browser headers, and no-store responses

## Local development

```bash
uv sync --group dev
uv run --dev pytest -q
```

Set required runtime configuration, then start:

```bash
export CARS_DATA_DIR=/tmp/cars-data
export CARS_INITIAL_PASSWORD_HASH='pbkdf2_sha256$...'
export CARS_SESSION_SECRET='at-least-32-characters'
export CARS_INGEST_TOKEN='at-least-32-characters'
export CARS_SECURE_COOKIES=false
uv run uvicorn cars_app.main:create_app --factory --host 127.0.0.1 --port 8780
```

## Sync the current vehicle watch

The sync script reads protected credentials without printing them:

```bash
uv run python scripts/sync_vehicle_watch.py \
  --contract /opt/data/.hermes/vehicle-watches/enclave-traverse-awd-clean-under-15000-48326.json \
  --saved /opt/data/cache/vehicle-search/saved-vehicle-final.json \
  --env-file /opt/data/secrets/cars-app.env
```

## Kubernetes

The Helm chart deploys one hardened replica using `Recreate`, a retained 1 GiB PVC, and an existing `cars-secrets` Secret. Required Secret keys:

- `CARS_INITIAL_PASSWORD_HASH`
- `CARS_SESSION_SECRET`
- `CARS_INGEST_TOKEN`

The app initializes its password hash once in SQLite. A password changed through the UI survives restarts and is not reset by the Kubernetes Secret.
