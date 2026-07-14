
import os
from pathlib import Path

#---------ollama (LLM/VLM + embeddings)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
CHAT_MODEL      = os.getenv("OLLAMA_MODEL", "qwen2.5vl:7b")
EMBED_MODEL     = os.getenv("EMBED_MODEL", "nomic-embed-text")

#---------model generation params--------
TEMPERATURE    = float(os.getenv("TEMPERATURE", "0.3"))
NUM_CTX        = int(os.getenv("NUM_CTX", "16384"))
NUM_PREDICT    = int(os.getenv("NUM_PREDICT", "1536"))     # cap output -> no runaway generation
REPEAT_PENALTY = float(os.getenv("REPEAT_PENALTY", "1.15"))  # discourage repeat loops

#----------Chroma (vector store/server mode)
CHROMA_HOST = os.getenv("CHROMA_HOST", "chroma")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))
COLLECTION  = os.getenv("COLLECTION", "multi_modal_rag")
ID_KEY      = "doc_id"

#------------storage (originals + images on the shared volume)
RAG_STORE     = Path(os.getenv("RAG_STORE", "/data/rag_store"))
DOCSTORE_PATH = RAG_STORE / "docstore.pkl"
