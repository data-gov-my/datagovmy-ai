from fastapi import Depends, FastAPI, status
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKey
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend

from redis import asyncio as aioredis

from langserve import add_routes
from langserve.schema import CustomUserType

from dotenv import load_dotenv
import logging
import os

from auth import APIKeyManager, get_token, get_master_token, key_manager
from schema import (
    ChatRequest,
    HealthCheck,
    TokenUpdate,
    TokenUpdateResponse,
    ChainType,
    Message,
)
from chain import create_new_chain

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

add_routes(
    app,
    rag_chain,
    path="/chat",
    dependencies=[Depends(get_token)],
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
