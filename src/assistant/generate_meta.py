import json
import os
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore
from langchain_openai.chat_models import ChatOpenAI
from langchain_openai.embeddings import OpenAIEmbeddings
from langchain_core.prompts.chat import ChatPromptTemplate
from langchain.schema import Document
import chromadb
from langchain_chroma import Chroma
from pydantic import BaseModel, Field
from langgraph.graph import START, StateGraph
from typing_extensions import List, TypedDict
from chromadb.config import Settings
import pandas as pd
from prompts import GENERATE_META_PROMPT, GENERATE_META_USER_PROMPT
from schema import State, OutputState, DatasetMetadata


class DCMetaRetriever(BaseRetriever):
    """Retriever for dataset metadata context"""

    vectorstore: VectorStore

    class Config:
        arbitrary_types_allowed = True

    def get_page_content(self, doc) -> str:
        """Create page_content to embed into context"""
        metadata = doc.metadata
        dc_meta = json.loads(metadata["header"])

        page_content = f"""Dataset: {dc_meta["id"]}
Description: {dc_meta["description"]}
Methodology: {dc_meta["data_methodology"]}
Caveat: {dc_meta["data_caveat"]}"""
        return page_content

    def _get_relevant_documents(self, query):
        docs = []
        for doc in self.vectorstore.max_marginal_relevance_search(
            query, k=4, filter={"source": "dc_meta"}
        ):
            content = self.get_page_content(doc)
            docs.append(Document(page_content=content, metadata=doc.metadata))
        return docs

    async def _aget_relevant_documents(self, query) -> List[Document]:
        docs_similar = await self.vectorstore.amax_marginal_relevance_search(
            query, k=4, filter={"source": "dc_meta"}
        )
        docs = []
        for doc in docs_similar:
            content = self.get_page_content(doc)
            docs.append(Document(page_content=content, metadata=doc.metadata))
        return docs


def build_generate_meta_graph():
    llm = ChatOpenAI(
        model="gpt-4o",
        temperature=0,
        streaming=True,
        verbose=True,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", GENERATE_META_PROMPT),
            ("user", GENERATE_META_USER_PROMPT),
        ]
    )

    embedding_llm = OpenAIEmbeddings(model="text-embedding-3-small")

    # init retriever
    client = chromadb.HttpClient(
        host=os.getenv("CHROMA_HOST"),
        port=os.getenv("CHROMA_PORT"),
        settings=Settings(),
    )

    db = Chroma(
        client=client,
        collection_name="dgmy_docs",
        embedding_function=embedding_llm,
    )
    dc_meta_retriever = DCMetaRetriever(vectorstore=db)

    def retrieve(state: State):
        print("retrieve")
        input_data = state["input_data"]
        query_string = f"{input_data['title_en']} {input_data['description_en']}"
        retrieved_docs = dc_meta_retriever.invoke(
            query_string, kwargs={"filter": {"source": "dc_meta"}}
        )
        return {"similar_datasets": retrieved_docs}

    def generate(state: State):
        print("generate")
        input_data = state["input_data"]
        similar_datasets = "\n\n".join(
            doc.page_content for doc in state["similar_datasets"]
        )
        if input_data["link_csv"]:
            df_sample = pd.read_csv(input_data["link_csv"])
            sample_rows = df_sample.sample(10).to_markdown()
        elif input_data["link_parquet"]:
            df_sample = pd.read_parquet(input_data["link_parquet"])
            sample_rows = df_sample.sample(10).to_markdown()
        else:
            sample_rows = ""

        chain = prompt | llm.with_structured_output(DatasetMetadata)
        res = chain.invoke(
            {
                "title_en": input_data["title_en"],
                "description_en": input_data["description_en"],
                "frequency": input_data["frequency"],
                "geography": input_data["geography"],
                "demography": input_data["demography"],
                "sample_rows": sample_rows,
                "similar_datasets": similar_datasets,
            }
        )
        return {"answer": res}

    graph_builder = StateGraph(State, output_schema=OutputState).add_sequence(
        [retrieve, generate]
    )
    graph_builder.add_edge(START, "retrieve")
    graph = graph_builder.compile()
    return graph
