from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from cars_app.auth import hash_password
from cars_app.config import Settings
from cars_app.main import create_app
from cars_app.storage import Store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        initial_password_hash=hash_password("correct-horse-battery-staple"),
        session_secret="s" * 64,
        ingest_token="i" * 48,
        secure_cookies=True,
        allowed_hosts=("testserver",),
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app, base_url="https://testserver")


def login(client: TestClient, password: str = "correct-horse-battery-staple") -> None:
    response = client.post(
        "/login",
        data={"username": "brett", "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def csrf_from_dashboard(client: TestClient) -> str:
    response = client.get("/")
    assert response.status_code == 200
    marker = 'name="csrf_token" value="'
    return response.text.split(marker, 1)[1].split('"', 1)[0]


def sample_contract() -> dict:
    return {
        "watch_id": "enclave-traverse-awd-clean-under-15000-48326",
        "criteria": {
            "models": ["Buick Enclave", "Chevrolet Traverse"],
            "max_price_usd": 15000,
            "max_mileage": 125000,
            "radius_miles": 60,
        },
        "known_records": [
            {
                "key": "vin:1GNEVTEST12345678",
                "record_key": "1GNEVTEST12345678",
                "vehicle": "2020 Chevrolet Traverse LT",
                "price_usd": 13999,
                "mileage": 105000,
                "url": "https://example.com/vehicle/1",
            }
        ],
        "seen_records": [
            {
                "key": "vin:1GNEVTEST12345678",
                "source": "CarGurus",
                "listing_id": "123",
                "vin": "1GNEVTEST12345678",
                "url": "https://example.com/vehicle/1",
                "year": 2020,
                "make": "Chevrolet",
                "model": "Traverse",
                "trim": "LT AWD",
                "price_usd": 13803,
                "body_mileage": 100137,
                "awd_evidence": "Exact listing says AWD.",
                "clean_title_evidence": "Exact listing says clean title.",
                "seller": "Example Motors",
                "stocking_location": "Auburn Hills, MI",
                "distance_miles_straight": 12.4,
                "last_verified_at": "2026-08-22T12:36:49Z",
            },
            {
                "key": "facebook:999",
                "source": "Facebook Marketplace",
                "listing_id": "999",
                "url": "https://facebook.com/marketplace/item/999",
                "year": 2022,
                "make": "Chevrolet",
                "model": "Traverse",
                "trim": "LT Cloth AWD",
                "price_usd": 14900,
                "body_mileage": 111000,
                "awd_evidence": "Seller description says AWD.",
                "clean_title_evidence": "Seller description says clean title.",
                "last_verified_at": "2026-08-21T12:23:21Z",
            },
        ],
        "last_successful_run_at": "2026-08-22T12:38:47Z",
        "last_run_coverage": {"complete": True, "new_exact_matches": 1},
        "last_run_outcome": {"status": "complete_new_matches_emailed"},
    }


def sample_saved() -> dict:
    return {
        "searched_at_utc": "2026-08-17T20:04:30+00:00",
        "vehicles": [
            {
                "id": "999",
                "title": "2022 Chevrolet Traverse LT Cloth AWD",
                "headline_price": 14900,
                "effective_price": 14900,
                "mileage": 111000,
                "location": "Warren, MI",
                "straight_line_miles": 17.7,
                "awd": "Explicit in seller description",
                "title_status": "Seller explicitly says clean title",
                "classification": "Near match",
                "blocking_gap": "1,000 miles over prior cap",
                "why_ranked": "Best confirmed fit.",
            }
        ],
    }


def test_dashboard_requires_login_and_sends_security_headers(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_login_rejects_wrong_password_and_accepts_correct_one(client: TestClient) -> None:
    denied = client.post("/login", data={"username": "brett", "password": "wrong"})
    assert denied.status_code == 401
    assert "Invalid username or password" in denied.text

    login(client)
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Vehicle Watch" in dashboard.text


def test_ingest_requires_token_and_deduplicates_collections(client: TestClient) -> None:
    payload = {"contract": sample_contract(), "saved": sample_saved()}

    denied = client.post("/api/import", json=payload)
    assert denied.status_code == 401

    accepted = client.post(
        "/api/import",
        json=payload,
        headers={"Authorization": "Bearer " + "i" * 48},
    )
    assert accepted.status_code == 200
    assert accepted.json()["vehicle_count"] == 2
    assert accepted.json()["collection_count"] == 3

    login(client)
    dashboard = client.get("/")
    assert "2020 Chevrolet Traverse LT AWD" in dashboard.text
    assert "2022 Chevrolet Traverse LT Cloth AWD" in dashboard.text
    assert "$13,803" in dashboard.text
    assert "Last successful search" in dashboard.text


def test_note_and_status_persist_across_store_reopen(client: TestClient, settings: Settings) -> None:
    payload = {"contract": sample_contract(), "saved": sample_saved()}
    client.post(
        "/api/import",
        json=payload,
        headers={"Authorization": "Bearer " + "i" * 48},
    )
    login(client)
    csrf = csrf_from_dashboard(client)

    response = client.post(
        "/vehicles/vin:1GNEVTEST12345678/note",
        data={"csrf_token": csrf, "note": "Schedule an independent inspection.", "status": "interested"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    reopened = Store(settings.database_path)
    annotation = reopened.get_annotation("vin:1GNEVTEST12345678")
    assert annotation is not None
    assert annotation["note"] == "Schedule an independent inspection."
    assert annotation["status"] == "interested"
    assert list((settings.data_dir / "backups").glob("cars-*.sqlite3"))


def test_import_refresh_does_not_overwrite_note(client: TestClient, settings: Settings) -> None:
    payload = {"contract": sample_contract(), "saved": sample_saved()}
    headers = {"Authorization": "Bearer " + "i" * 48}
    client.post("/api/import", json=payload, headers=headers)
    login(client)
    csrf = csrf_from_dashboard(client)
    client.post(
        "/vehicles/facebook:999/note",
        data={"csrf_token": csrf, "note": "Ask for service records.", "status": "researching"},
    )

    changed = sample_contract()
    changed["seen_records"][1]["price_usd"] = 14500
    client.post("/api/import", json={"contract": changed, "saved": sample_saved()}, headers=headers)

    reopened = Store(settings.database_path)
    vehicle = reopened.get_vehicle("facebook:999")
    annotation = reopened.get_annotation("facebook:999")
    assert vehicle["price_usd"] == 14500
    assert annotation["note"] == "Ask for service records."
    assert annotation["status"] == "researching"


def test_note_is_bounded_and_csrf_is_required(client: TestClient) -> None:
    headers = {"Authorization": "Bearer " + "i" * 48}
    client.post("/api/import", json={"contract": sample_contract()}, headers=headers)
    login(client)

    no_csrf = client.post(
        "/vehicles/facebook:999/note",
        data={"note": "unsafe", "status": "new"},
    )
    assert no_csrf.status_code == 403

    csrf = csrf_from_dashboard(client)
    too_long = client.post(
        "/vehicles/facebook:999/note",
        data={"csrf_token": csrf, "note": "x" * 5001, "status": "new"},
    )
    assert too_long.status_code == 422


def test_password_change_invalidates_existing_sessions(client: TestClient) -> None:
    login(client)
    csrf = csrf_from_dashboard(client)
    response = client.post(
        "/account/password",
        data={
            "csrf_token": csrf,
            "current_password": "correct-horse-battery-staple",
            "new_password": "a-different-secure-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login?changed=1"

    old_session = client.get("/", follow_redirects=False)
    assert old_session.status_code == 303
    denied = client.post(
        "/login",
        data={"username": "brett", "password": "correct-horse-battery-staple"},
    )
    assert denied.status_code == 401
    login(client, "a-different-secure-password")


def test_readiness_is_storage_aware(app, settings: Settings) -> None:
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert client.get("/ready").json() == {"status": "ok", "app": "cars"}

    for child in settings.data_dir.iterdir():
        child.unlink()
    settings.data_dir.rmdir()
    settings.data_dir.write_text("blocked")

    failed = client.get("/ready")
    assert failed.status_code == 503
    assert failed.json() == {"detail": "Storage unavailable."}


def test_probe_routes_bypass_host_filter_but_dashboard_does_not(app) -> None:
    client = TestClient(app, base_url="https://untrusted.invalid")

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 400
