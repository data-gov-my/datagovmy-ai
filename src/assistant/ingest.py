"""This is a script to refresh the weaviate index.
Will be a replacing the entire dagster pipeline
For docs AI:
1. Load docs data from mdx files: docs (github) and local
2. Load DC metadata from parquet
3. Combine these two
4. Dump into record manager as single index

Downside is need to load and parse everything regardless.
"""

import weaviate
import requests
import pandas as pd
from dotenv import load_dotenv

from langchain.indexes import SQLRecordManager, index
from langchain.docstore.document import Document
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores.weaviate import Weaviate

from config import *
from loaders import MdxLoader, DashboardMetaLoader, DCMetaLoader
from vect import WeaviateVectorDB
from utils.helpers import send_telegram

load_dotenv()

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
            settings.GITHUB_REPO, settings.GITHUB_PATH, settings.GITHUB_TOKEN
        ),
        mdx_type="docs",
    )
    df_mdx_docs = mdx_loader.load()

    # load local mdx files to augment
    mdx_local_loader = MdxLoader(
        get_github_mdx("data-gov-my/datagovmy-ai", "data", settings.GITHUB_TOKEN),
        mdx_type="local",
    )
    df_mdx_local = mdx_local_loader.load()

    # load DC metadata
    dc_meta_loader = DCMetaLoader(
        [settings.DC_META_PARQUET, settings.DC_METAFIELDS_PARQUET]
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


def run_index(docs, class_name):
    # connect to weaviate vectorstore
    oai_embeddings = OpenAIEmbeddings()
    client = weaviate.Client(url=settings.WEAVIATE_URL)
    attributes = ["header", "source"]
    weaviate_lc = Weaviate(
        client,
        class_name,  # capitalized
        "text",  # constant
        embedding=oai_embeddings,
        attributes=attributes,
        by_text=False,  # force vector search
    )

    # initialise record manager
    conn_str = settings.REC_MGR_CONN_STR
    namespace = f"weaviate/{class_name}"
    record_manager = SQLRecordManager(namespace, db_url=conn_str)
    record_manager.create_schema()

    index_result = index(
        docs, record_manager, weaviate_lc, cleanup="full", source_id_key="source"
    )
    if (
        index_result["num_added"] > 0
        or index_result["num_updated"] > 0
        or index_result["num_deleted"] > 0
    ):
        send_telegram(f"Vector index updated for {class_name}: {index_result}")


if __name__ == "__main__":
    try:
        mdx_docs = load_mdx_docs()
        run_index(mdx_docs, settings.DOCS_VINDEX)
    except Exception as e:
        send_telegram(f"Error in docs ingest: {e}")
