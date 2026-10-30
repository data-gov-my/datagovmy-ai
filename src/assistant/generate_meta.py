import asyncio
import json
import logging

import pandas as pd
from langchain_core.documents import Document
from langchain_core.prompts.chat import ChatPromptTemplate
from langgraph.graph import START, StateGraph
from langgraph.types import RetryPolicy
from typing_extensions import List

from models import get_generate_meta_llm, get_vectorstore
from prompts import GENERATE_META_PROMPT, GENERATE_META_USER_PROMPT
from schema import State, OutputState, DatasetMetadata

logger = logging.getLogger(__name__)

SIMILAR_DATASETS_K = 4
SAMPLE_ROWS = 10


def format_dc_meta(doc: Document) -> str:
    """Create page_content to embed into context"""
    dc_meta = json.loads(doc.metadata["header"])
    return f"""Dataset: {dc_meta["id"]}
Description: {dc_meta["description"]}
Methodology: {dc_meta["data_methodology"]}
Caveat: {dc_meta["data_caveat"]}
Data Fields: {dc_meta["col_meta_clean"]}"""


async def search_similar_datasets(query: str) -> List[Document]:
    docs = await get_vectorstore().amax_marginal_relevance_search(
        query, k=SIMILAR_DATASETS_K, filter={"source": "dc_meta"}
    )
    return [
        Document(page_content=format_dc_meta(doc), metadata=doc.metadata)
        for doc in docs
    ]


def read_sample_rows(input_data: dict) -> str:
    """Sample rows from the dataset file, if a link is provided"""
    if input_data.get("link_csv"):
        df = pd.read_csv(input_data["link_csv"])
    elif input_data.get("link_parquet"):
        df = pd.read_parquet(input_data["link_parquet"])
    else:
        return ""
    return df.sample(min(SAMPLE_ROWS, len(df))).to_markdown()


def build_generate_meta_graph():
    llm = get_generate_meta_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", GENERATE_META_PROMPT),
            ("user", GENERATE_META_USER_PROMPT),
        ]
    )
    chain = prompt | llm.with_structured_output(DatasetMetadata)

    async def retrieve(state: State):
        input_data = state["input_data"]
        query_string = f"{input_data['title_en']} {input_data['description_en']}"
        return {"similar_datasets": await search_similar_datasets(query_string)}

    async def generate(state: State):
        input_data = state["input_data"]
        similar_datasets = "\n\n".join(
            doc.page_content for doc in state["similar_datasets"]
        )
        # blocking download + parse, keep it off the event loop
        sample_rows = await asyncio.to_thread(read_sample_rows, input_data)
        res = await chain.ainvoke(
            {
                "title_en": input_data["title_en"],
                "description_en": input_data["description_en"],
                "frequency": input_data.get("frequency", ""),
                "geography": input_data.get("geography", ""),
                "demography": input_data.get("demography", ""),
                "sample_rows": sample_rows,
                "similar_datasets": similar_datasets,
            }
        )
        return {"answer": res}

    graph_builder = (
        StateGraph(State, output_schema=OutputState)
        .set_node_defaults(retry_policy=RetryPolicy(max_attempts=2))
        .add_sequence([retrieve, generate])
    )
    graph_builder.add_edge(START, "retrieve")
    return graph_builder.compile(name="GenerateMeta")


def build_translation_keys(dataset_meta: DatasetMetadata):
    trans_en = {}
    trans_ms = {}
    for field in dataset_meta.fields:
        trans_en[field.name] = field.title_en
        trans_ms[field.name] = field.title_ms
    return trans_en, trans_ms
