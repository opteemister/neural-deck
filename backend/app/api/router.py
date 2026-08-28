"""HTTP API router for the application shell."""

from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/health/live", tags=["health"])
async def health_live() -> dict[str, str]:
    """Return a process-level liveness response."""

    return {"status": "ok"}
