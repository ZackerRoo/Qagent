"""Local-system research endpoints under the app's existing access boundary."""
from typing import Literal

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from qagent.config import get_settings
from qagent.providers.documented_research import DocumentedResearch


class SafeValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Default validation errors echo input, which may contain misplaced keys.
                return JSONResponse(status_code=422, content={
                    "status": "error", "error": "invalid_request", "research_only": True,
                    "decision_weight": False, "activation_allowed": False})
        return safe_handler


class ResearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["datahubco", "promax"]
    api: str = Field(min_length=1, max_length=80)
    params: dict[str, object] = Field(default_factory=dict, max_length=100)
    limit: StrictInt = Field(default=100, ge=1, le=5000)
    offset: StrictInt = Field(default=0, ge=0, le=1000000)
    fields: str | None = Field(default=None, max_length=10000)


router = APIRouter(prefix="/documented-research", tags=["documented-research"], route_class=SafeValidationRoute)


@router.get("/catalogue")
def catalogue():
    return DocumentedResearch(get_settings()).catalogue()


@router.post("/query")
def query(body: ResearchQuery):
    return DocumentedResearch(get_settings()).query(**body.model_dump())
