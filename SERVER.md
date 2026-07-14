# SERVER — architecture & operations

How the ONGC Manual RAG runs on a server, and how to operate it. Pair this with
[RUN.md](RUN.md) for the exact commands.

---

## 1. What this is
A fully-local, offline Retrieval-Augmented-Generation service over a software
manual (PDF). An **admin** ingests the manual once; **users** ask "how do I…"
questions through a web page and get a step-by-step guide + a workflow DAG.
No external APIs, no internet at runtime.

---

## 2. Services (containers)

| Service | GPU | Always on | Role |
|---|---|---|---|
| `ollama` | ✅ (only one) | yes | Serves `qwen2.5vl:7b` (LLM+vision) and `nomic-embed-text` (embeddings). |
| `chroma` | ❌ | yes | Vector database (summary vectors). |
| `api` | ❌ | yes | FastAPI — the RAG chain. Endpoints: `/query`, `/health`, `/admin/reload`. |
| `frontend` | ❌ | yes | Static web page (nginx) that proxies `/api` to `api`. |
| `ingestion` | ❌ | on demand | Builds the index from a PDF (admin only). Runs, then exits. |

Only `ollama` uses the GPU. The whole stack needs **< 7 GB VRAM** → runs on 1 GPU,
scales to many later.

```
 user ─ http:8080 ─► frontend ─/api─► api ──► chroma   (vectors)
                                       │  └──► rag_store (originals + images)
                                       └────► ollama   (VLM + embeddings, GPU)
 admin ─ ingestion ─► partition→summarise→embed ─► chroma + rag_store
```

---

## 3. Models — vendored & offline

All weights are bundled in `./models` (no downloads at runtime):

| Path | Models | Used by |
|---|---|---|
| `models/ollama/` | `qwen2.5vl:7b`, `nomic-embed-text` (GGUF blobs + manifests) | ollama |
| `models/hf/` | `yolox` (layout), `table-transformer` (table structure) | ingestion |

- `ollama` mounts `./models/ollama` at `/root/.ollama/models` → models already
  present, **no `ollama pull`**.
- `ingestion` mounts `./models/hf` at `/models` with `HF_HUB_OFFLINE=1` →
  **no HuggingFace download**.

These weights are platform-neutral (GGUF / ONNX / safetensors), so the same
`./models` folder works on any x86-64 Linux server.

> The folder is ~6 GB and is git-ignored. **Transfer it with the project via
> `scp`/`rsync`/tar — do not commit it.**

---

## 4. Persistence (Docker volumes)

| Volume | Holds | Survives `down`? |
|---|---|---|
| `chroma_data` | vector index | yes |
| `rag_store` | `docstore.pkl` (originals) + `extracted_images/` | yes |
| `./models` (bind mount) | model weights | yes (it's a folder) |

The index = **vectors (chroma) + originals (rag_store)**, joined by `doc_id`.
Ingestion wipes and rewrites both together, so they never drift.

---

## 5. Configuration

All settings are environment variables in `docker-compose.yml`; the code reads
them via `services/api/app/config.py`. Key ones:

| Var | Default | Meaning |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://ollama:11434` | where the model server is |
| `OLLAMA_MODEL` | `qwen2.5vl:7b` | the LLM/VLM |
| `EMBED_MODEL` | `nomic-embed-text` | the embedding model |
| `CHROMA_HOST` / `CHROMA_PORT` | `chroma` / `8000` | vector DB |
| `RAG_STORE` | `/data/rag_store` | originals + images |

Change a port/model/host here — never in the Python files.

---

## 6. Operating it

**First deploy:** copy the `ongc-rag/` folder (with `models/`) to the server →
`docker compose up -d --build` → ingest a manual → `/admin/reload`. (See RUN.md.)

**Add / update a manual:** drop the PDF in `manuals/`, run the `ingestion`
service, then `POST /admin/reload`. Ingestion rebuilds the whole index.

**After a new manual, always call** `POST /admin/reload` — the api caches the
originals in memory and reloads them only on this call (or on restart).

**Health:** `GET /health` → `{"status":"ok"}`. Use for load-balancer checks.

---

## 7. Scaling beyond 1 GPU (later)
Swap `ollama` for **vLLM** (VLM) + **TEI** (embeddings) on more GPUs and point
`OLLAMA_BASE_URL` / `EMBED_MODEL` at them. Move the docstore to **Postgres** and
images to **MinIO**, run multiple `api` replicas behind nginx. The chain code in
`services/api` does not change — only config/endpoints.

---

## 8. Every file in `ongc-rag/` — what it does

### Top level
| File | What it does |
|---|---|
| `docker-compose.yml` | **Production** stack: defines all 5 services (ollama+GPU, chroma, api, frontend, ingestion), their env, ports, and the vendored-model + volume mounts. This is what you run on the server. |
| `docker-compose.test.yml` | **Local test** stack (no GPU): same services **minus** ollama — points api/ingestion at the *host's* Ollama via `host.docker.internal`. For laptop testing only. |
| `.gitignore` | Keeps the 6 GB `models/` and admin PDFs out of git (ship those out-of-band). |
| `README.md` | Short overview + quick start. |
| `RUN.md` | Every command (start, ingest, query, manage, stop) for both stacks. |
| `SERVER.md` | This file — architecture, models, ops, file reference. |
| `manuals/.gitkeep` | Keeps the empty `manuals/` folder in git; admin drops PDFs here. |
| `models/ollama/` | Vendored Ollama weights (`qwen2.5vl:7b` + `nomic-embed-text`): `blobs/` (sha256 weight files) + `manifests/`. Mounted into the ollama container. |
| `models/hf/` | Vendored HuggingFace models for ingestion (`yolox` layout + `table-transformer`). Mounted into the ingestion container. |

### `services/api/` — the query backend (always-on, CPU, lightweight)
| File | What it does |
|---|---|
| `Dockerfile` | Builds the api image: python-slim, `pip install` the requirements, copy `app/`, run uvicorn. |
| `requirements.txt` | Only what the api imports: fastapi, uvicorn, langchain, langchain-ollama, langchain-chroma, chromadb. (No unstructured/torch → stays small.) |
| `app/__init__.py` | Empty marker that makes `app/` an importable Python package. |
| `app/config.py` | **All settings in one place**, read from env (Ollama URL, model names, Chroma host/port, storage paths). The other files import from here. |
| `app/store.py` | Builds the `MultiVectorRetriever`: connects to the Chroma server + loads `docstore.pkl` (originals). |
| `app/rag.py` | The RAG chain (notebook `cell-rag`): `parse_docs`, `build_prompt`, the LCEL `chain`, and the `ChatOllama` model. |
| `app/main.py` | FastAPI app + routes: `GET /health`, `POST /query` (streams the answer), `POST /admin/reload` (re-reads the index after an ingest). |

### `services/ingestion/` — the index builder (admin-only, runs then exits)
| File | What it does |
|---|---|
| `Dockerfile` | Heavy image: installs tesseract + poppler + OpenGL libs (for unstructured hi_res), then `pip install` the requirements. |
| `requirements.txt` | unstructured[all-docs], langchain, langchain-ollama, langchain-chroma, chromadb. |
| `ingest.py` | The whole ingestion pipeline (notebook cells partition→separate→summarise→store): reads the PDF, summarises tables/images via the VLM, embeds, and writes the Chroma collection + `docstore.pkl` + images. Stores **plain strings/dicts** so the api needs no `unstructured`. |

### `services/frontend/` — the web question box (always-on, nginx)
| File | What it does |
|---|---|
| `Dockerfile` | Copies the page + nginx config into the nginx image. |
| `index.html` | The single-page UI: a question box that POSTs to `/api/query` and streams the answer back. |
| `nginx.conf` | Serves the page and **reverse-proxies `/api/*` to the api container** (so the browser needs no CORS). |

---

## 9. Limits / notes
- **Image build needs internet once** (pip + apt). Runtime is fully offline.
- Concurrency on 1 GPU is modest (a handful of simultaneous vision queries);
  raise `OLLAMA_NUM_PARALLEL` or add GPUs to grow.
- Phase 2 (live on-screen guidance) is **not** part of this server — it needs a
  desktop client. This deployment is Phase 1 (Q&A) only.
