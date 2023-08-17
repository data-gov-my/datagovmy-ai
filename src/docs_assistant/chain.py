import weaviate
from langchain.chains import RetrievalQA, RetrievalQAWithSourcesChain

from prompts import *
from schema import *
from config import *


def create_chain(
    messages: list[Message],
) -> RetrievalQAWithSourcesChain:
    from langchain.chat_models import ChatOpenAI
    from langchain.embeddings.openai import OpenAIEmbeddings
    from langchain.memory import ConversationBufferMemory
    from langchain.memory.chat_message_histories.in_memory import ChatMessageHistory
    from langchain.chains import RetrievalQA, RetrievalQAWithSourcesChain
    from langchain.vectorstores.weaviate import Weaviate
    from langchain.chains.qa_with_sources.loading import load_qa_with_sources_chain
    from langchain.prompts.chat import (
        ChatPromptTemplate,
        HumanMessagePromptTemplate,
        MessagesPlaceholder,
        SystemMessagePromptTemplate,
    )

    chat_prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessagePromptTemplate.from_template(QA_DOCS_ASSISTANT_ALT),
            MessagesPlaceholder(variable_name="history"),
            HumanMessagePromptTemplate.from_template("{query}"),
        ]
    )

    chat_memory = ChatMessageHistory()
    for message in messages:
        if message.role == Role.USER:
            chat_memory.add_user_message(message.content)
        elif message.role == Role.ASSISTANT:
            chat_memory.add_ai_message(message.content)

    memory = ConversationBufferMemory(
        chat_memory=chat_memory,
        return_messages=True,
        memory_key="history",
        input_key="query",
    )

    embedding_llm = OpenAIEmbeddings(
        openai_api_key=settings.OPENAI_API_KEY,
        openai_organization=settings.OPENAI_ORG_ID,
    )

    llm = ChatOpenAI(
        model="gpt-3.5-turbo",
        max_tokens=1000,
        temperature=0,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_organization=settings.OPENAI_ORG_ID,
        streaming=True,
        verbose=True,
    )

    # connect to weaviate instance
    client = weaviate.Client(url=settings.WEAVIATE_URL)
    attributes = ["header", "source"]
    weaviate_docs = Weaviate(
        client,
        settings.DOCS_INDEX,  # capitalized
        "text",  # constant
        embedding=embedding_llm,
        attributes=attributes,
        by_text=False,  # force vector search
    )

    qa_chain_docs = load_qa_with_sources_chain(
        llm=llm, chain_type="stuff", prompt=chat_prompt, memory=memory, verbose=True
    )
    retrival_qa_chain_docs = RetrievalQAWithSourcesChain(
        combine_documents_chain=qa_chain_docs,
        retriever=weaviate_docs.as_retriever(),
        question_key="query",
    )

    return retrival_qa_chain_docs
