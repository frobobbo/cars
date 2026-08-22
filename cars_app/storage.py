from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
  vehicle_key TEXT PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'Vehicle watch',
  listing_id TEXT,
  vin TEXT,
  url TEXT,
  title TEXT NOT NULL,
  year INTEGER,
  make TEXT,
  model TEXT,
  trim TEXT,
  price_usd INTEGER,
  mileage INTEGER,
  seller TEXT,
  location TEXT,
  distance_miles REAL,
  awd_evidence TEXT,
  title_evidence TEXT,
  classification TEXT,
  blocking_gap TEXT,
  risk_notes TEXT,
  first_seen_at TEXT,
  last_verified_at TEXT,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_vehicles_verified ON vehicles(last_verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_vehicles_price ON vehicles(price_usd);

CREATE TABLE IF NOT EXISTS vehicle_collections (
  vehicle_key TEXT NOT NULL REFERENCES vehicles(vehicle_key) ON DELETE CASCADE,
  collection TEXT NOT NULL,
  label TEXT NOT NULL,
  added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(vehicle_key, collection)
);

CREATE TABLE IF NOT EXISTS annotations (
  vehicle_key TEXT PRIMARY KEY REFERENCES vehicles(vehicle_key) ON DELETE CASCADE,
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'new',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

TEXT_FIELDS = (
    "source",
    "listing_id",
    "vin",
    "url",
    "title",
    "make",
    "model",
    "trim",
    "seller",
    "location",
    "awd_evidence",
    "title_evidence",
    "classification",
    "blocking_gap",
    "risk_notes",
    "first_seen_at",
    "last_verified_at",
)
NUMERIC_FIELDS = ("year", "price_usd", "mileage", "distance_miles")
ALL_FIELDS = ("vehicle_key",) + TEXT_FIELDS + NUMERIC_FIELDS + ("details_json",)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_from_url(url: str | None) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    if "facebook" in host:
        return "Facebook Marketplace"
    if "cargurus" in host:
        return "CarGurus"
    if "cars.com" in host:
        return "Cars.com"
    if "autotrader" in host:
        return "AutoTrader"
    return "Vehicle watch"


def _title_from_record(record: dict[str, Any]) -> str:
    if record.get("vehicle"):
        return str(record["vehicle"])
    parts = [record.get("year"), record.get("make"), record.get("model"), record.get("trim")]
    return " ".join(str(part).strip() for part in parts if part not in {None, ""}) or "Vehicle listing"


def _known_record(record: dict[str, Any]) -> dict[str, Any]:
    key = _clean_text(record.get("key"))
    if not key:
        raise ValueError("Known vehicle record is missing a stable key.")
    url = _clean_text(record.get("url"))
    vin = key[4:] if key.startswith("vin:") else None
    listing_id = key.split(":", 1)[1] if ":" in key and not vin else record.get("record_key")
    return {
        "vehicle_key": key,
        "source": _source_from_url(url),
        "listing_id": listing_id,
        "vin": vin,
        "url": url,
        "title": _title_from_record(record),
        "price_usd": record.get("price_usd"),
        "mileage": record.get("mileage"),
        "details_json": record,
    }


def _seen_record(record: dict[str, Any]) -> dict[str, Any]:
    key = _clean_text(record.get("key"))
    if not key:
        raise ValueError("Seen vehicle record is missing a stable key.")
    return {
        "vehicle_key": key,
        "source": record.get("source") or _source_from_url(record.get("url")),
        "listing_id": record.get("listing_id") or record.get("record_key"),
        "vin": record.get("vin"),
        "url": record.get("url"),
        "title": _title_from_record(record),
        "year": record.get("year"),
        "make": record.get("make"),
        "model": record.get("model"),
        "trim": record.get("trim"),
        "price_usd": record.get("price_usd"),
        "mileage": record.get("body_mileage") or record.get("mileage"),
        "seller": record.get("seller"),
        "location": record.get("stocking_location") or record.get("location"),
        "distance_miles": record.get("distance_miles_straight") or record.get("distance_miles"),
        "awd_evidence": record.get("awd_evidence"),
        "title_evidence": record.get("clean_title_evidence") or record.get("title_evidence"),
        "risk_notes": record.get("source_conflict") or record.get("history_note") or record.get("fee_note"),
        "first_seen_at": record.get("first_seen_at"),
        "last_verified_at": record.get("last_verified_at"),
        "details_json": record,
    }


def _saved_record(record: dict[str, Any], searched_at: str | None) -> dict[str, Any]:
    vin = _clean_text(record.get("vin"))
    listing_id = _clean_text(record.get("id"))
    if not vin and not listing_id:
        raise ValueError("Saved vehicle is missing both VIN and listing ID.")
    key = f"vin:{vin.upper()}" if vin else f"facebook:{listing_id}"
    return {
        "vehicle_key": key,
        "source": "Facebook Marketplace",
        "listing_id": listing_id,
        "vin": vin.upper() if vin else None,
        "url": f"https://www.facebook.com/marketplace/item/{listing_id}/" if listing_id else None,
        "title": record.get("title") or "Saved Marketplace vehicle",
        "price_usd": record.get("effective_price") or record.get("headline_price"),
        "mileage": record.get("mileage"),
        "location": record.get("location"),
        "distance_miles": record.get("straight_line_miles"),
        "awd_evidence": record.get("awd"),
        "title_evidence": record.get("title_status"),
        "classification": record.get("classification"),
        "blocking_gap": record.get("blocking_gap"),
        "risk_notes": record.get("condition_risks"),
        "last_verified_at": searched_at,
        "details_json": record,
    }


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self.init()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    def init(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def verify_connection(self) -> None:
        with self.connect() as con:
            con.execute("SELECT 1").fetchone()

    def ensure_password_hash(self, encoded: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO app_settings(key,value) VALUES('password_hash',?)",
                (encoded,),
            )
            con.execute(
                "INSERT OR IGNORE INTO app_settings(key,value) VALUES('auth_revision','1')"
            )

    def password_state(self) -> tuple[str, int]:
        with self.connect() as con:
            rows = dict(con.execute("SELECT key,value FROM app_settings").fetchall())
        return rows["password_hash"], int(rows.get("auth_revision", "1"))

    def change_password(self, encoded: str) -> int:
        with self.connect() as con:
            revision = int(
                con.execute(
                    "SELECT value FROM app_settings WHERE key='auth_revision'"
                ).fetchone()[0]
            ) + 1
            con.execute(
                "UPDATE app_settings SET value=? WHERE key='password_hash'", (encoded,)
            )
            con.execute(
                "UPDATE app_settings SET value=? WHERE key='auth_revision'", (str(revision),)
            )
        self.backup()
        return revision

    def _upsert_vehicle(self, con: sqlite3.Connection, item: dict[str, Any]) -> None:
        key = _clean_text(item.get("vehicle_key"))
        title = _clean_text(item.get("title"))
        if not key or not title:
            raise ValueError("Vehicle key and title are required.")
        previous = con.execute(
            "SELECT details_json FROM vehicles WHERE vehicle_key=?", (key,)
        ).fetchone()
        details: dict[str, Any] = {}
        if previous:
            try:
                details.update(json.loads(previous["details_json"] or "{}"))
            except json.JSONDecodeError:
                pass
        incoming_details = item.get("details_json") or {}
        if isinstance(incoming_details, dict):
            details.update(incoming_details)
        values: dict[str, Any] = {"vehicle_key": key, "details_json": json.dumps(details, sort_keys=True)}
        for field in TEXT_FIELDS:
            values[field] = _clean_text(item.get(field))
        for field in NUMERIC_FIELDS:
            values[field] = item.get(field)

        columns = ",".join(ALL_FIELDS)
        placeholders = ",".join(f":{field}" for field in ALL_FIELDS)
        updates = []
        for field in TEXT_FIELDS:
            updates.append(f"{field}=COALESCE(NULLIF(excluded.{field},''),vehicles.{field})")
        for field in NUMERIC_FIELDS:
            updates.append(f"{field}=COALESCE(excluded.{field},vehicles.{field})")
        updates.extend(
            [
                "details_json=excluded.details_json",
                "imported_at=CURRENT_TIMESTAMP",
            ]
        )
        con.execute(
            f"INSERT INTO vehicles ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(vehicle_key) DO UPDATE SET {','.join(updates)}",
            values,
        )

    def import_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        contract = payload.get("contract") or {}
        saved = payload.get("saved") or {}
        known = contract.get("known_records") or []
        seen = contract.get("seen_records") or []
        saved_records = saved.get("vehicles") or []
        if sum(map(len, (known, seen, saved_records))) > 2000:
            raise ValueError("Import exceeds the 2,000-record safety limit.")

        with self.connect() as con:
            # Import the older saved-comparison snapshot first. The daily watch
            # then wins conflicts with its fresher exact-listing evidence.
            searched_at = _clean_text(saved.get("searched_at_utc"))
            for raw in saved_records:
                vehicle = _saved_record(raw, searched_at)
                self._upsert_vehicle(con, vehicle)
                con.execute(
                    "INSERT INTO vehicle_collections(vehicle_key,collection,label) VALUES(?,?,?) "
                    "ON CONFLICT(vehicle_key,collection) DO UPDATE SET label=excluded.label",
                    (vehicle["vehicle_key"], "saved", "Saved comparison"),
                )
            for raw in known:
                vehicle = _known_record(raw)
                self._upsert_vehicle(con, vehicle)
                con.execute(
                    "INSERT INTO vehicle_collections(vehicle_key,collection,label) VALUES(?,?,?) "
                    "ON CONFLICT(vehicle_key,collection) DO UPDATE SET label=excluded.label",
                    (vehicle["vehicle_key"], "watch", "Daily exact match"),
                )
            for raw in seen:
                vehicle = _seen_record(raw)
                self._upsert_vehicle(con, vehicle)
                con.execute(
                    "INSERT INTO vehicle_collections(vehicle_key,collection,label) VALUES(?,?,?) "
                    "ON CONFLICT(vehicle_key,collection) DO UPDATE SET label=excluded.label",
                    (vehicle["vehicle_key"], "watch", "Daily exact match"),
                )

            metadata = {
                "watch_id": contract.get("watch_id"),
                "criteria": contract.get("criteria"),
                "last_successful_run_at": contract.get("last_successful_run_at")
                or contract.get("last_successful_search_utc"),
                "last_run_coverage": contract.get("last_run_coverage"),
                "last_run_outcome": contract.get("last_run_outcome"),
                "saved_searched_at": saved.get("searched_at_utc"),
            }
            for key, value in metadata.items():
                if value is None:
                    continue
                encoded = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
                con.execute(
                    "INSERT INTO watch_meta(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                    (key, encoded),
                )

            vehicle_count = con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
            collection_count = con.execute("SELECT COUNT(*) FROM vehicle_collections").fetchone()[0]
        return {"vehicle_count": vehicle_count, "collection_count": collection_count}

    def list_vehicles(
        self, *, collection: str = "all", status: str = "all", query: str = ""
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if collection != "all":
            clauses.append(
                "EXISTS (SELECT 1 FROM vehicle_collections f WHERE f.vehicle_key=v.vehicle_key AND f.collection=?)"
            )
            params.append(collection)
        if status != "all":
            clauses.append("COALESCE(a.status,'new')=?")
            params.append(status)
        if query.strip():
            clauses.append(
                "(v.title LIKE ? OR v.seller LIKE ? OR v.location LIKE ? OR v.vin LIKE ? OR a.note LIKE ?)"
            )
            needle = f"%{query.strip()}%"
            params.extend([needle] * 5)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as con:
            rows = con.execute(
                f"""
                SELECT v.*, COALESCE(a.note,'') AS note,
                       COALESCE(a.status,'new') AS user_status,
                       a.updated_at AS note_updated_at,
                       GROUP_CONCAT(c.collection) AS collections,
                       GROUP_CONCAT(c.label) AS collection_labels
                FROM vehicles v
                LEFT JOIN annotations a ON a.vehicle_key=v.vehicle_key
                LEFT JOIN vehicle_collections c ON c.vehicle_key=v.vehicle_key
                {where}
                GROUP BY v.vehicle_key
                ORDER BY
                  CASE COALESCE(a.status,'new')
                    WHEN 'interested' THEN 0 WHEN 'researching' THEN 1
                    WHEN 'new' THEN 2 WHEN 'contacted' THEN 3
                    WHEN 'passed' THEN 4 WHEN 'sold' THEN 5 ELSE 6 END,
                  COALESCE(v.last_verified_at,v.imported_at) DESC,
                  v.price_usd ASC
                LIMIT 500
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_vehicle(self, vehicle_key: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM vehicles WHERE vehicle_key=?", (vehicle_key,)
            ).fetchone()
        return dict(row) if row else None

    def save_annotation(self, vehicle_key: str, note: str, status: str) -> None:
        with self.connect() as con:
            exists = con.execute(
                "SELECT 1 FROM vehicles WHERE vehicle_key=?", (vehicle_key,)
            ).fetchone()
            if not exists:
                raise KeyError(vehicle_key)
            con.execute(
                "INSERT INTO annotations(vehicle_key,note,status,updated_at) "
                "VALUES(?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(vehicle_key) DO UPDATE SET note=excluded.note,status=excluded.status,updated_at=CURRENT_TIMESTAMP",
                (vehicle_key, note, status),
            )
        self.backup()

    def get_annotation(self, vehicle_key: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM annotations WHERE vehicle_key=?", (vehicle_key,)
            ).fetchone()
        return dict(row) if row else None

    def stats(self) -> dict[str, int]:
        with self.connect() as con:
            total = con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
            watch = con.execute(
                "SELECT COUNT(*) FROM vehicle_collections WHERE collection='watch'"
            ).fetchone()[0]
            saved = con.execute(
                "SELECT COUNT(*) FROM vehicle_collections WHERE collection='saved'"
            ).fetchone()[0]
            notes = con.execute(
                "SELECT COUNT(*) FROM annotations WHERE note<>''"
            ).fetchone()[0]
            interested = con.execute(
                "SELECT COUNT(*) FROM annotations WHERE status='interested'"
            ).fetchone()[0]
        return {
            "total": total,
            "watch": watch,
            "saved": saved,
            "notes": notes,
            "interested": interested,
        }

    def metadata(self) -> dict[str, Any]:
        with self.connect() as con:
            rows = con.execute("SELECT key,value,updated_at FROM watch_meta").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            value: Any = row["value"]
            if value and value[0] in "[{":
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            result[row["key"]] = value
            result["metadata_updated_at"] = row["updated_at"]
        return result

    def backup(self) -> Path:
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = backup_dir / f"cars-{stamp}.sqlite3"
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
        backups = sorted(backup_dir.glob("cars-*.sqlite3"), reverse=True)
        for old in backups[20:]:
            old.unlink(missing_ok=True)
        return destination
