from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    initial_password_hash: str
    session_secret: str
    ingest_token: str
    secure_cookies: bool = True
    allowed_hosts: tuple[str, ...] = ("cars.johnsons.casa", "localhost", "127.0.0.1")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "cars.sqlite3"

    @classmethod
    def from_env(cls) -> "Settings":
        hosts = tuple(
            host.strip()
            for host in os.getenv(
                "CARS_ALLOWED_HOSTS", "cars.johnsons.casa,localhost,127.0.0.1"
            ).split(",")
            if host.strip()
        )
        settings = cls(
            data_dir=Path(os.getenv("CARS_DATA_DIR", "/data")),
            initial_password_hash=os.getenv("CARS_INITIAL_PASSWORD_HASH", "").strip(),
            session_secret=os.getenv("CARS_SESSION_SECRET", "").strip(),
            ingest_token=os.getenv("CARS_INGEST_TOKEN", "").strip(),
            secure_cookies=os.getenv("CARS_SECURE_COOKIES", "true").lower()
            not in {"0", "false", "no"},
            allowed_hosts=hosts,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.initial_password_hash.startswith("pbkdf2_sha256$"):
            raise RuntimeError("CARS_INITIAL_PASSWORD_HASH is not configured.")
        if len(self.session_secret) < 32:
            raise RuntimeError("CARS_SESSION_SECRET must contain at least 32 characters.")
        if len(self.ingest_token) < 32:
            raise RuntimeError("CARS_INGEST_TOKEN must contain at least 32 characters.")
        if not self.allowed_hosts:
            raise RuntimeError("At least one allowed host is required.")
