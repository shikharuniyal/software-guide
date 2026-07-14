""" 
run do the mannuak ingestion run == docker compose run --rm ingestion /data/manuals/Word_manual.pdf
"""
import os
import sys
import uuid
import base64
import pickle
import shutil
from pathlib import Path

import chromadb
from unstructured.partition.pdf import partition_pdf
from langchain_chroma import Chroma
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_classic.retrievers.multi_vector import MultiVectorRetriever
from langchain_classic.storage import InMemoryStore


#env params (not shared by img/ filesystem )
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434") 
CHROMA_HOST = os.getenv("CHROMA_HOST", "chroma") 
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
CHAT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5vl:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
RAG_STORE = Path(os.getenv("RAG_STORE", "/data/rag_store"))
IMG_DIR = RAG_STORE / "extracted_images"
DOCSTORE_PATH = RAG_STORE / "docstore.pkl"

COLLECTION = "multi_modal_rag"
ID_KEY = "doc_id"

model = ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, temperature=0.3, num_ctx=16384)
embeddings = OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)


def extract(pdf_path):
    chunks = partition_pdf(
        filename=pdf_path,
        infer_table_structure=True,
        strategy="hi_res",
        extract_image_block_types=["Image"],
        extract_image_block_to_payload=True,
        hi_res_model_name="yolox_quantized",
        chunking_strategy="by_title",
        max_characters=10000,
        combine_text_under_n_chars=2000,
        new_after_n_chars=6000,
    )
    texts, tables = [], []
    for c in chunks:
        if "Table" in str(type(c)):
            tables.append(c)
        elif "CompositeElement" in str(type(c)):
            texts.append(c)

    images = []
    for c in chunks:
        if "CompositeElement" in str(type(c)):
            for el in c.metadata.orig_elements:
                if "Image" in str(type(el)):
                    images.append(el.metadata.image_base64)
    return texts, tables, images


def summarise_tables(tables_html):
    prompt = ChatPromptTemplate.from_template(
        "You are an assistant tasked with summarizing tables.\n"
        "Give a concise summary of the table so it can be found by search.\n"
        "Respond only with the summary, no additional comment.\n\nTable (HTML): {element}"
    )
    chain = {"element": lambda x: x} | prompt | model | StrOutputParser()
    out = []
    for html in tables_html:
        try:
            out.append(chain.invoke(html))
        except Exception as e:
            out.append(f"[Summary unavailable: {e}]")
    return out


def summarise_images(images):
    action = (
        "This image is from a software manual. Extract it for a step-by-step guide. "
        "List, using the EXACT on-screen text: visible menu paths (e.g. File > Export), "
        "button labels, icon names and their location, field/parameter names and any "
        "values shown, and any numbered steps. If it is a diagram or chart, state what it "
        "depicts and the key labels. Be concrete and literal; do not summarise vaguely."
    )
    prompt = ChatPromptTemplate.from_messages([
        ("user", [
            {"type": "text", "text": action},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{image}"}},
        ]),
    ])
    chain = prompt | model | StrOutputParser()
    out = []
    for b64 in images:
        try:
            out.append(chain.invoke({"image": b64}))
        except Exception as e:
            out.append(f"[Image unavailable: {e}]")
    return out


def main(pdf_path):
    if not Path(pdf_path).exists():
        sys.exit(f"PDF not found: {pdf_path}")

    print(f"[1/4] Extracting {pdf_path} ...")
    texts, tables, images = extract(pdf_path)
    print(f"      {len(texts)} texts | {len(tables)} tables | {len(images)} images")

    print("[2/4] Summarising tables + images (VLM) ...")
    text_summaries = [t.text for t in texts]                 # raw text = best search key
    tables_html = [t.metadata.text_as_html for t in tables]
    table_summaries = summarise_tables(tables_html)
    image_summaries = summarise_images(images)

    print("[3/4] Wiping old index + writing new one ...")
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    try:
        client.delete_collection(COLLECTION)                 # keep vectors + docstore in sync
    except Exception:
        pass
    shutil.rmtree(IMG_DIR, ignore_errors=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)

    vectorstore = Chroma(client=client, collection_name=COLLECTION, embedding_function=embeddings)
    store = InMemoryStore()
    retriever = MultiVectorRetriever(vectorstore=vectorstore, docstore=store, id_key=ID_KEY)

    def add(summaries, originals):
        if not summaries:
            return
        ids = [str(uuid.uuid4()) for _ in originals]
        docs = [Document(page_content=s, metadata={ID_KEY: ids[i]}) for i, s in enumerate(summaries)]
        retriever.vectorstore.add_documents(docs)
        retriever.docstore.mset(list(zip(ids, originals)))

    # originals are PLAIN strings / dicts so the api never needs `unstructured`
    add(text_summaries, text_summaries)                      # text original = the text itself
    add(table_summaries, tables_html)                        # table original = its HTML

    image_refs = []
    for i, b64 in enumerate(images):
        p = IMG_DIR / f"img_{i}.jpg"
        p.write_bytes(base64.b64decode(b64))
        summ = image_summaries[i] if i < len(image_summaries) else ""
        image_refs.append({"img_path": str(p), "summary": summ})
    add(image_summaries, image_refs)

    print("[4/4] Saving docstore ...")
    with open(DOCSTORE_PATH, "wb") as f:
        pickle.dump(store.store, f)

    n_vec = vectorstore._collection.count()
    print(f"Done. Indexed {n_vec} vectors | {len(store.store)} docstore items "
          f"({'in sync' if n_vec == len(store.store) else 'MISMATCH!'}).")
    print("Now call  POST /admin/reload  on the api so it picks up the new manual.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python ingest.py /data/manuals/<file>.pdf")
    main(sys.argv[1])
