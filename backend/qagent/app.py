from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from qagent.api.routes import (
    restore_automation_scheduler_from_storage,
    restore_historical_backfill_from_storage,
    router,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    restore_historical_backfill_from_storage()
    restore_automation_scheduler_from_storage()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Qagent API", version="0.1.0", lifespan=lifespan)
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
    return app


app = create_app()
