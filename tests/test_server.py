"""API smoke tests: access-key guard for LAN clients and HTTP range support for video."""
from __future__ import annotations

from fastapi.testclient import TestClient

from gymcut.server import _range_file, access_key, app


def test_lan_clients_need_the_access_key():
    # TestClient's host is "testclient" = not local. LAN access is HTTPS-only (secure cookie).
    c = TestClient(app, base_url="https://testserver")
    assert c.get("/api/info").status_code == 401
    key = access_key()
    r = c.get(f"/api/info?k={key}")
    assert r.status_code == 200
    assert "gk" in r.cookies or c.cookies.get("gk")
    assert c.get("/api/info").status_code == 200   # cookie now set


def test_static_assets_are_public():
    c = TestClient(app)
    assert c.get("/static/css/app.css").status_code == 200


def test_range_requests(tmp_path):
    from starlette.requests import Request
    p = tmp_path / "x.bin"
    p.write_bytes(bytes(range(256)) * 100)

    def req(rng):
        scope = {"type": "http", "headers": [(b"range", rng.encode())] if rng else [], "method": "GET", "path": "/"}
        return Request(scope)

    r = _range_file(p, req("bytes=10-19"), "application/octet-stream")
    assert r.status_code == 206 and r.body == bytes(range(10, 20))
    assert r.headers["content-range"] == "bytes 10-19/25600"
    r = _range_file(p, req("bytes=-5"), "application/octet-stream")
    assert r.body == (bytes(range(256)) * 100)[-5:]
    r = _range_file(p, req("bytes=99999-"), "application/octet-stream")
    assert r.status_code == 416
