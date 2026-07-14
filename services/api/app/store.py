#Build the MultiVectorRetriever

#params in config.py.

import pickle

import chromadb
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings
from langchain_classic.retrievers.multi_vector import MultiVectorRetriever
from langchain_classic.storage import InMemoryStore

from . import config


def build_retriever():
    embeddings = OllamaEmbeddings(model=config.EMBED_MODEL, base_url=config.OLLAMA_BASE_URL)

    client = chromadb.HttpClient(host=config.CHROMA_HOST, port=config.CHROMA_PORT)
    vectorstore = Chroma(
        client=client,
        collection_name=config.COLLECTION,
        embedding_function=embeddings,
    )

    store = InMemoryStore()
    if config.DOCSTORE_PATH.exists():
        with open(config.DOCSTORE_PATH, "rb") as f:
            store.store = pickle.load(f)

    return MultiVectorRetriever(vectorstore=vectorstore, docstore=store, id_key=config.ID_KEY)
