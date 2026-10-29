"""Shared model and vectorstore factories.

Model ids and reasoning effort can be overridden through env. GPT-6 models are
reasoning models and reject `temperature`; reasoning effort "none" is the
closest match to the previous non-reasoning gpt-4.1 behaviour.
"""

import os
from functools import lru_cache
from typing import Optional

import chromadb
from chromadb.config import Settings
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PGVector

ANSWER_MODEL = os.getenv("OPENAI_ANSWER_MODEL") or "gpt-6-sol"
ANSWER_REASONING_EFFORT = os.getenv("OPENAI_ANSWER_REASONING_EFFORT") or "none"
FAST_MODEL = os.getenv("OPENAI_FAST_MODEL") or "gpt-6-luna"
FAST_REASONING_EFFORT = os.getenv("OPENAI_FAST_REASONING_EFFORT") or "none"
GENERATE_META_MODEL = os.getenv("OPENAI_GENERATE_META_MODEL") or "gpt-6-sol"
GENERATE_META_REASONING_EFFORT = (
    os.getenv("OPENAI_GENERATE_META_REASONING_EFFORT") or "low"
)
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536

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


@lru_cache(maxsize=4)
def get_vectorstore(
    database_url: Optional[str] = None, async_mode: bool = True
) -> VectorStore:
    """Shared vectorstore, created on first use rather than at import so the
    API can boot while the store is unavailable.

    PostgreSQL + pgvector when a database URL is given or DATABASE_URL is set
    (ECS platform); Chroma otherwise (Singapore host). The API searches
    asynchronously; ingest passes its own URL with async_mode=False, because
    the indexing API is synchronous.
    """
    url = database_url or os.getenv("DATABASE_URL")
    if url:
        return pgvector_store(url, async_mode=async_mode)
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


def pgvector_store(
    url: str, async_mode: bool, embeddings: Optional[Embeddings] = None
) -> PGVector:
    # The extension is created by db_bootstrap.py; the API's database user is
    # read-only and couldn't create it anyway
    return PGVector(
        embeddings=embeddings or OpenAIEmbeddings(model=EMBEDDING_MODEL),
        connection=url,
        collection_name=COLLECTION_NAME,
        embedding_length=EMBEDDING_DIMENSIONS,
        use_jsonb=True,
        create_extension=False,
        async_mode=async_mode,
        engine_args={"pool_pre_ping": True},
    )
