from fastapi import Depends, FastAPI, status
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKey
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend

from redis import asyncio as aioredis

from langserve import add_routes
from langserve.schema import CustomUserType

from langchain_core.runnables import RunnableConfig

from dotenv import load_dotenv
import logging
import os

from auth import APIKeyManager, get_token, get_master_token, key_manager
from schema import (
    ChatRequest,
    GenerateMetaRequest,
    HealthCheck,
    TokenUpdate,
    TokenUpdateResponse,
    ChainType,
    Message,
    GenerateMetaResponse,
    DatasetMetadata,
)
from chain import create_new_chain
from generate_meta import build_generate_meta_graph, build_translation_keys

load_dotenv()

# maximum history of messages for memory
MAX_MESSAGES = 5


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.args and len(record.args) >= 3 and record.args[2] != "/health"


logging.getLogger("uvicorn.access").addFilter(EndpointFilter())


class NewChatReqest(CustomUserType):
    # chain_type: ChainType = ChainType.DOCS
    messages: list


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = await aioredis.from_url("redis://host.docker.internal:6381")
    FastAPICache.init(RedisBackend(redis), prefix="")
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("BACKEND_CORS_ORIGINS"),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rag_chain = create_new_chain()
generate_meta_graph = build_generate_meta_graph()

add_routes(
    app,
    rag_chain,
    path="/chat",
    dependencies=[Depends(get_token)],
)


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


@app.get("/health", response_model=HealthCheck)
def get_health():
    return HealthCheck(status="OK")


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
