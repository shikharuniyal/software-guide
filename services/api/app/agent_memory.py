import json
import hashlib
import chromadb
import chromadb.utils.embedding_functions as embedding_functions
from . import config

client = chromadb.HttpClient(host=config.CHROMA_HOST, port=config.CHROMA_PORT)
offline_ef = embedding_functions.OllamaEmbeddingFunction(
    url=f"{config.OLLAMA_BASE_URL}/api/embeddings", model_name=config.EMBED_MODEL
)
memory_collection = client.get_or_create_collection(name="agent_memory", embedding_function=offline_ef)

def get_state_hash(goal, ui_hint):
    return hashlib.md5(f"{goal}||{ui_hint}".encode('utf-8')).hexdigest()

def check_success_state(state_hash):
    """TIER 2: Check if this screen is already verified as a 'Success State' for this goal."""
    res = memory_collection.get(ids=[f"success_{state_hash}"])
    if res and res.get("documents") and len(res["documents"]) > 0:
        return True
    return False

def save_success_state(state_hash, goal, reasoning):
    """Saves a new verified success state."""
    memory_collection.upsert(
        ids=[f"success_{state_hash}"],
        documents=[json.dumps({"status": "COMPLETE", "instruction": reasoning})],
        metadatas=[{"goal": goal, "type": "success"}]
    )

def get_cached_action(state_hash):
    res = memory_collection.get(ids=[f"action_{state_hash}"])
    if res and res.get("documents") and len(res["documents"]) > 0:
        return json.loads(res["documents"][0])
    return None

def save_action(state_hash, goal, action_json):
    memory_collection.upsert(
        ids=[f"action_{state_hash}"],
        documents=[json.dumps(action_json)],
        metadatas=[{"goal": goal, "type": "action"}]
    )