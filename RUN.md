# RUN — all essential commands

> **Windows PowerShell note:** `curl` is an alias for `Invoke-WebRequest` and does
> NOT accept `-X`/`-d`. Either use **`curl.exe`** (real curl, ships with Windows)
> for every `curl` command below, or use the PowerShell-native form, e.g.:
> ```powershell
> Invoke-RestMethod -Method Post -Uri http://localhost:8000/admin/reload
> Invoke-RestMethod -Method Post -Uri http://localhost:8000/query `
>   -ContentType "application/json" -Body '{"question":"How do I insert a table?"}'
> ```
> On the Linux server (bash), the plain `curl` commands work as written.

Two ways to run:
- **Production (GPU server):** uses `docker-compose.yml` — Ollama runs in a GPU container.
- **Local test (no GPU):** uses `docker-compose.test.yml` — uses the Ollama already running on your host.

Run every command from inside the `ongc-rag/` folder.

---

## A. Production (GPU server)

> Models are **vendored** in `./models` (qwen2.5vl + nomic for Ollama, yolox +
> table-transformer for ingestion). So there is **no `ollama pull` and no HF
> download** — runtime is fully offline. Just make sure `./models` came with the
> folder (~6 GB; transferred via scp/rsync/tar, since it's git-ignored).

```bash
# 1. Build + start all services (ollama already has its models from ./models)
docker compose up -d --build

# 2. Put the PDF in ./manuals/ then build the index (admin, per manual)
docker compose run --rm ingestion /data/manuals/Word_manual.pdf

# 3. Tell the api to load the new index
curl -X POST http://localhost:8000/admin/reload

# 4. Use it
#    open http://localhost:8080  in a browser
```

---

## B. Local test (no GPU — uses host Ollama)

> Requires Ollama running on the host with both models already pulled
> (`ollama pull qwen2.5vl:7b` and `ollama pull nomic-embed-text`).

```bash
# 1. Build + start (chroma, api, frontend) — no ollama container
docker compose -f docker-compose.test.yml up -d --build

# 2. Build the index from a PDF
#    NOTE (Windows Git Bash only): prefix MSYS_NO_PATHCONV=1 so /data/... isn't rewritten
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.test.yml run --rm ingestion /data/manuals/Word_manual.pdf

# 3. Reload + use
curl -X POST http://localhost:8000/admin/reload
#    open http://localhost:8080
```

---

## C. Query from the terminal (instead of the browser)

```bash
# streamed answer
curl -N -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I insert a table?"}'

# health check
curl http://localhost:8000/health
```

---

## D. Manage / inspect

```bash
docker compose ps                 # what's running          (add -f docker-compose.test.yml for the test stack)
docker compose logs -f api        # follow api logs
docker compose logs -f ollama     # follow model server logs
docker compose restart api        # restart one service
docker compose exec ollama ollama list   # models available in Ollama
```

---

## E. Re-ingest a different / updated manual

```bash
# drop the new PDF in ./manuals/ then:
docker compose run --rm ingestion /data/manuals/<your-file>.pdf   # wipes + rebuilds the index
curl -X POST http://localhost:8000/admin/reload
```

---

## F. Stop / clean up

```bash
docker compose down            # stop + remove containers (KEEPS the index/volumes)
docker compose down -v         # also delete volumes (index, vectors, downloaded models) = clean slate

# test stack:
docker compose -f docker-compose.test.yml down
docker compose -f docker-compose.test.yml down -v
```

---

## Ports
| URL | What |
|---|---|
| http://localhost:8080 | web UI |
| http://localhost:8000 | api (`/query`, `/health`, `/admin/reload`) |
| http://localhost:8001 | chroma |
| http://localhost:11434 | ollama |
