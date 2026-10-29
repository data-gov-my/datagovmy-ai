from fastapi import Depends, FastAPI, HTTPException, status
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKey
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend

from redis import asyncio as aioredis

from langserve import add_routes
from sse_starlette.sse import EventSourceResponse

from langchain_core.runnables import RunnableConfig

from dotenv import load_dotenv
import json
import logging
import os

from auth import APIKeyManager, get_token, get_master_token, key_manager
from schema import (
    ChatRequest,
    GenerateMetaRequest,
    HealthCheck,
    TokenUpdate,
    TokenUpdateResponse,
    GenerateMetaResponse,
)
from chain import build_rag_graph, create_chat_runnable, stream_chat_events
from generate_meta import build_generate_meta_graph, build_translation_keys

load_dotenv()


def parse_cors_origins(value: str | None) -> list[str]:
    """Accept a JSON list or a comma-separated string"""
    if not value:
        return []
    if value.strip().startswith("["):
        return json.loads(value)
    return [origin.strip() for origin in value.split(",")]


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.args and len(record.args) >= 3 and record.args[2] != "/health"


logging.getLogger("uvicorn.access").addFilter(EndpointFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = await aioredis.from_url("redis://host.docker.internal:6381")
    FastAPICache.init(RedisBackend(redis), prefix="")
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(os.getenv("BACKEND_CORS_ORIGINS")),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rag_graph = build_rag_graph()
generate_meta_graph = build_generate_meta_graph()

# LangServe contract kept for the existing frontend: {"messages": [...]} in,
# answer string (streamed) out
add_routes(
    app,
    create_chat_runnable(rag_graph),
    path="/chat",
    dependencies=[Depends(get_token)],
)


@app.post("/v2/chat/stream", dependencies=[Depends(get_token)])
async def chat_stream(payload: ChatRequest):
    """SSE stream of `sources` (list of {title, url}) then `token` events"""

    async def events():
        async for event in stream_chat_events(
            rag_graph, payload.model_dump(), RunnableConfig(run_name="RAGChain")
        ):
            yield {"event": event["type"], "data": json.dumps(event["data"])}
        yield {"event": "end", "data": ""}

    return EventSourceResponse(events())


@app.post("/generate-meta", response_model=GenerateMetaResponse)
async def generate_meta(payload: GenerateMetaRequest):
    config = RunnableConfig(
        metadata={"langsmith_project": os.getenv("LANGCHAIN_PROJECT_GENMETA")},
    )
    input_data = payload.input_data
    res = await generate_meta_graph.ainvoke({"input_data": input_data}, config=config)
    dataset_meta = res["answer"]
    # post process - build translation keys
    trans_en, trans_ms = build_translation_keys(dataset_meta)
    dataset_meta.translations_en = trans_en
    dataset_meta.translations_ms = trans_ms
    return GenerateMetaResponse(
        metadata=dataset_meta,
    )


@app.post(
    "/auth-token",
    response_model=TokenUpdateResponse,
)
async def update_token(
    payload: TokenUpdate, api_key: APIKey = Depends(get_master_token)
):
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )
    await key_manager.update_key(payload.ROLLING_TOKEN)
    return TokenUpdateResponse(message="Auth token received.")


@app.get(
    "/health",
    summary="Health Check for ELB",
    response_description="Return HTTP Status Code 200 (OK)",
    status_code=status.HTTP_200_OK,
    response_model=HealthCheck,
)
def get_health():
    return HealthCheck(status="OK")
