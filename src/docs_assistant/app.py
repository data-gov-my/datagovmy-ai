#!/usr/bin/env python3
import logging
from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKey

from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend

from redis import asyncio as aioredis

from lanarky.responses import StreamingResponse
from lanarky.routing import LangchainRouter, LLMCacheMode

from dotenv import load_dotenv

from config import *
from auth import APIKeyManager, get_token, get_master_token, key_manager
from schema import ChatRequest, HealthCheck, TokenUpdate, TokenUpdateResponse, ChainType
from chain import create_chain

load_dotenv()

# maximum history of messages for memory
MAX_MESSAGES = 5


class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.args and len(record.args) >= 3 and record.args[2] != "/health"


logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

router = LangchainRouter(llm_cache_mode=LLMCacheMode.IN_MEMORY)
# router = LangchainRouter()


@router.post(
    "/chat",
    summary="OpenAPI Docs Assistant",
    description="Chat with the open API documentation assistant",
)
def chat(request: ChatRequest, api_key: APIKey = Depends(get_token)):
    # take only latest MAX_MESSAGES
    chain = create_chain(
        chain_type=request.chain_type, messages=request.messages[-MAX_MESSAGES - 1 : -1]
    )
    return StreamingResponse.from_chain(chain, request.messages[-1].content)


@router.post(
    "/auth-token",
    response_model=TokenUpdateResponse,
    responses={status.HTTP_401_UNAUTHORIZED: dict(model=TokenUpdateResponse)},
)
async def update_token(
    payload: TokenUpdate, api_key: APIKey = Depends(get_master_token)
):
    await key_manager.update_key(payload.ROLLING_TOKEN)
    return TokenUpdateResponse(message="Auth token received.")


@router.get(
    "/health",
    summary="Health Check for ELB",
    response_description="Return HTTP Status Code 200 (OK)",
    status_code=status.HTTP_200_OK,
    response_model=HealthCheck,
)
def get_health():
    return HealthCheck(status="OK")


app = FastAPI()
app.include_router(router, tags=["chat"])

origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    redis = aioredis.from_url("redis://")
    FastAPICache.init(RedisBackend(redis), prefix="")
