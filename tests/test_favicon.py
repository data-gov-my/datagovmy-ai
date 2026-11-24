from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.favicon import FAVICON, router


def test_favicon_is_served():
    app = FastAPI()
    app.include_router(router)
    res = TestClient(app).get("/favicon.ico")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/x-icon"
    assert "max-age" in res.headers["cache-control"]
    assert res.content == FAVICON.read_bytes()
    # An ICO file starts with reserved 0, type 1
    assert res.content[:4] == b"\x00\x00\x01\x00"
