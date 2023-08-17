#!/usr/bin/env python3

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from lanarky.responses import StreamingResponse
from lanarky.routing import LangchainRouter, LLMCacheMode

from dotenv import load_dotenv

from config import *
from schema import *
from chain import create_chain


load_dotenv()

# maximum history of messages for memory
MAX_MESSAGES = 5

# router = LangchainRouter(llm_cache_mode=LLMCacheMode.IN_MEMORY)
router = LangchainRouter()


@router.post(
    "/chat",
    summary="OpenAPI Docs Assistant",
    description="Chat with the openAPI documentation assistant",
)
def chat(request: ChatRequest):
    chain = create_chain(messages=request.messages[-MAX_MESSAGES - 1 : -1])
    return StreamingResponse.from_chain(chain, request.messages[-1].content)


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
