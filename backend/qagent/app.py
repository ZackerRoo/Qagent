from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from qagent.api.fuyao_routes import router as fuyao_router
from qagent.api.routes import (
    _shutdown_automation_scheduler_loop,
    _terminate_paper_dual_track_executor,
    _terminate_full_market_executor,
    restore_automation_scheduler_from_storage,
    restore_full_market_scan_job_from_storage,
    restore_historical_backfill_from_storage,
    restore_paper_dual_track_jobs_from_storage,
    restore_walk_forward_job_from_storage,
    router,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    restore_historical_backfill_from_storage()
    restore_walk_forward_job_from_storage()
    restore_full_market_scan_job_from_storage()
    restore_paper_dual_track_jobs_from_storage()
    restore_automation_scheduler_from_storage()
    try:
        yield
    finally:
        # Stop only the in-memory loop. The persisted enabled flag intentionally
        # remains unchanged so the next process can resume the same schedule.
        _shutdown_automation_scheduler_loop()
        _terminate_full_market_executor()
        _terminate_paper_dual_track_executor()


def create_app() -> FastAPI:
    app = FastAPI(title="Qagent API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1_000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5174",
            "http://localhost:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api")
    app.include_router(fuyao_router, prefix="/api")
    return app


app = create_app()
