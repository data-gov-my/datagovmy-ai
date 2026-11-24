"""Serves /favicon.ico, the data.gov.my icon, so browsers and link previews
that look it up on this host (ai.datagovmy.app) find one."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

FAVICON = Path(__file__).resolve().parents[1] / "static" / "favicon.ico"

router = APIRouter()


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(
        FAVICON,
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )
