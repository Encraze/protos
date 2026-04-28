import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_reports_db_failure(client, monkeypatch):
    from app.health import router as health_router

    async def _raise(_session):
        raise RuntimeError("db down")

    monkeypatch.setattr(health_router, "_check_db", _raise)
    response = await client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["detail"]["status"] == "error"
