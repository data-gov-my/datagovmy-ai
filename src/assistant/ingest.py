"""This is a script to refresh the vector index (pgvector, or Chroma on the old host).
Will be a replacing the entire dagster pipeline
For docs AI:
1. Load docs data from mdx files: docs (github) and local
2. Load DC metadata from parquet
3. Combine these two
4. Dump into record manager as single index

Downside is need to load and parse everything regardless.
"""

import os
import requests
import pandas as pd
from contextlib import contextmanager
from typing import Iterator, List, Optional
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from langchain_classic.indexes import SQLRecordManager
from langchain_core.indexing import index
from langchain_core.documents import Document

from loaders import MdxLoader, DashboardMetaLoader, DCMetaLoader
from models import get_vectorstore
from utils.helpers import send_telegram

load_dotenv()

print("GITHUB_REPO", os.getenv("GITHUB_REPO"))
# Connection strings carry passwords: never print them

INGEST_LOCK_KEY = 742001  # arbitrary, unique to this job

DOCS_MDX_FIELDS = ["header", "source"]
DASH_META_FIELDS = ["name", "description", "category", "agency", "source"]


def get_github_mdx(repo: str, folder_path: str, token: str) -> List:
    """Recurisve function to get list of mdx files from git"""
    api_url = f"https://api.github.com/repos/{repo}/contents/{folder_path}"
    headers = {"Authorization": f"token {token}"}
    response = requests.get(api_url, headers=headers)
    response.raise_for_status()

    contents = response.json()

    # Initialize a list to store file paths
    file_paths = []

    for item in contents:
        if item["type"] == "file" and item["name"].endswith(".en.mdx"):
            file_paths.append(item["download_url"])
        elif item["type"] == "dir":
            # Recursively get files from subfolders
            subfolder_path = f"{folder_path}/{item['name']}"
            subfolder_files = get_github_mdx(repo, subfolder_path, token)
            file_paths.extend(subfolder_files)

    return file_paths


def load_mdx_docs() -> List:
    """Load documents for Docs Assistant"""
    # load mdx files from git
    mdx_loader = MdxLoader(
        get_github_mdx(
            os.getenv("GITHUB_REPO"),
            os.getenv("GITHUB_PATH"),
            os.getenv("GITHUB_TOKEN"),
        ),
        mdx_type="docs",
    )
    df_mdx_docs = mdx_loader.load()

    # load local mdx files to augment
    mdx_local_loader = MdxLoader(
        get_github_mdx("data-gov-my/datagovmy-ai", "data", os.getenv("GITHUB_TOKEN")),
        mdx_type="local",
    )
    df_mdx_local = mdx_local_loader.load()

    # load DC metadata
    dc_meta_loader = DCMetaLoader(
        [os.getenv("DC_META_PARQUET"), os.getenv("DC_METAFIELDS_PARQUET")]
    )
    df_dcmeta = dc_meta_loader.load()

    # combine
    dfm = pd.concat([df_mdx_docs, df_mdx_local, df_dcmeta], ignore_index=True)
    docs = dfm.apply(
        lambda row: Document(
            page_content=row["content_embed"],
            metadata={"header": row["header"], "source": row["source"]},
        ),
        axis=1,
    ).tolist()
    return docs


@contextmanager
def single_run_lock(database_url: Optional[str]) -> Iterator[bool]:
    """Postgres advisory lock so a scheduled run and a docs-triggered run
    can't index at the same time. Released when the connection closes, even
    if the task is killed. No-op on the Chroma setup (single cron)."""
    if not database_url:
        yield True
        return
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": INGEST_LOCK_KEY}
            ).scalar()
            yield bool(acquired)
    finally:
        engine.dispose()


def run_index(docs, class_name):
    # ECS platform: vectors and the record manager share one PostgreSQL
    # database, so they can't drift apart. Singapore host: Chroma + its own
    # record manager database.
    ingest_url = os.getenv("DATABASE_URL_INGEST")
    if ingest_url:
        print("Indexing into PostgreSQL (pgvector)")
        vectorstore = get_vectorstore(ingest_url, async_mode=False)
        conn_str, namespace = ingest_url, f"pgvector/{class_name}"
    else:
        print("Connecting to Chroma DB at", os.getenv("CHROMA_HOST"))
        vectorstore = get_vectorstore()
        conn_str, namespace = os.getenv("REC_MGR_CONN_STR"), f"chroma/{class_name}"

    record_manager = SQLRecordManager(namespace, db_url=conn_str)
    record_manager.create_schema()

    index_result = index(
        docs, record_manager, vectorstore, cleanup="full", source_id_key="source"
    )
    print(f"Current index contains: {len(record_manager.list_keys())} records")
    if (
        index_result["num_added"] > 0
        or index_result["num_updated"] > 0
        or index_result["num_deleted"] > 0
    ):
        print(f"Vector index updated for {class_name}: {index_result}")
        # send_telegram(f"Vector index updated for {class_name}: {index_result}")
    else:
        print("No changes in vector index")
        # send_telegram("No changes in vector index")


if __name__ == "__main__":
    with single_run_lock(os.getenv("DATABASE_URL_INGEST")) as acquired:
        if not acquired:
            print("Another ingest run is in progress; skipping")
            exit(0)
        try:
            print("Loading MDX documents...")
            mdx_docs = load_mdx_docs()
            print(f"Loaded {len(mdx_docs)} documents")

            print("Running index...")
            run_index(mdx_docs, os.getenv("DOCS_VINDEX"))
            print("Index completed successfully")
        except Exception as e:
            print(f"Error in docs ingest: {e}")
            import traceback

            traceback.print_exc()
            # send_telegram(f"Error in docs ingest: {e}")
            exit(1)
