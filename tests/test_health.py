from pathlib import Path

from fastapi.testclient import TestClient

from glosswork.app import create_app
from glosswork.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", embedding_enabled=False)


def test_healthz(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_readyz_after_migrations(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
