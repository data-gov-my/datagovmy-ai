"""Shared model and vectorstore factories.

Model ids and reasoning effort can be overridden through env. GPT-6 models are
reasoning models and reject `temperature`; reasoning effort "none" is the
closest match to the previous non-reasoning gpt-4.1 behaviour.
"""

import os
from functools import lru_cache

import chromadb
from chromadb.config import Settings
from langchain_chroma import Chroma
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

ANSWER_MODEL = os.getenv("OPENAI_ANSWER_MODEL") or "gpt-6-sol"
ANSWER_REASONING_EFFORT = os.getenv("OPENAI_ANSWER_REASONING_EFFORT") or "none"
FAST_MODEL = os.getenv("OPENAI_FAST_MODEL") or "gpt-6-luna"
FAST_REASONING_EFFORT = os.getenv("OPENAI_FAST_REASONING_EFFORT") or "none"
GENERATE_META_MODEL = os.getenv("OPENAI_GENERATE_META_MODEL") or "gpt-6-sol"
GENERATE_META_REASONING_EFFORT = (
    os.getenv("OPENAI_GENERATE_META_REASONING_EFFORT") or "low"
)
EMBEDDING_MODEL = "text-embedding-3-small"

COLLECTION_NAME = "dgmy_docs"


def get_answer_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=ANSWER_MODEL,
        reasoning_effort=ANSWER_REASONING_EFFORT,
        streaming=True,
        max_retries=2,
    )


def get_fast_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=FAST_MODEL, reasoning_effort=FAST_REASONING_EFFORT, max_retries=2
    )


def get_generate_meta_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=GENERATE_META_MODEL,
        reasoning_effort=GENERATE_META_REASONING_EFFORT,
        max_retries=2,
    )


@lru_cache(maxsize=1)
def get_vectorstore() -> Chroma:
    """Shared Chroma vectorstore, created on first use rather than at import so
    the API can boot while Chroma is unavailable."""
    client = chromadb.HttpClient(
        host=os.getenv("CHROMA_HOST"),
        port=os.getenv("CHROMA_PORT"),
        settings=Settings(),
    )
    return Chroma(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding_function=OpenAIEmbeddings(model=EMBEDDING_MODEL),
    )
