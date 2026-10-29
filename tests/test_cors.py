import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from utils.helpers import parse_cors_origins


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, []),
        ("", []),
        ('["https://a.gov.my", "https://b.gov.my"]', ["https://a.gov.my", "https://b.gov.my"]),
        ("https://a.gov.my, https://b.gov.my", ["https://a.gov.my", "https://b.gov.my"]),
        # The live value: a JSON list wrapped in single quotes
        ("'[\"*\"]'", ["*"]),
        ('"https://a.gov.my/"', ["https://a.gov.my"]),
        ('["https://a.gov.my/"]', ["https://a.gov.my"]),
        ("https://a.gov.my,,", ["https://a.gov.my"]),
    ],
)  # fmt: skip
def test_parse_cors_origins(value, expected):
    assert parse_cors_origins(value) == expected


def preflight(origins_value: str, origin: str):
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_origins(origins_value),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return TestClient(app).options(
        "/chat/stream",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_live_value_allows_the_frontend_like_the_old_deployment():
    response = preflight("'[\"*\"]'", "https://developer.data.gov.my")
    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "https://developer.data.gov.my"
    )


def test_explicit_list_still_rejects_other_origins():
    value = '["https://developer.data.gov.my"]'
    assert preflight(value, "https://developer.data.gov.my").status_code == 200
    assert preflight(value, "https://evil.example.com").status_code == 400
