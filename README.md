# Software Guide — Local Manual Assistant

> ⭐ If you find this project useful, please consider starring the repo. This
> is the **first fully working iteration** and the project is still under
> active development — issues and pull requests are very welcome.

A fully **local, offline** assistant built from a software manual (PDF).

- **Ask (web):** type a question, get a step-by-step answer + a workflow diagram.
- **Guide (desktop):** a Windows app watches your screen and highlights the exact
  next thing to click, verifying each step actually worked before moving on.

Everything runs on your own hardware — one local AI model, no cloud APIs, no
internet needed at runtime. Only the `ollama` service uses a GPU (~7 GB VRAM);
everything else is CPU.

> **Plug-and-play.** It runs on the built-in defaults with zero config edits.
> The commands below work as-is. The **only** things you ever change are:
> 1. your **PDF filename** in the ingest command (Part C), and
> 2. for the desktop guide from another PC, the **server IP** (Part E).
>
> Everything else — model names, ports, image names — already has working
> defaults. Editing `.env` is optional (see [Configuration](#9-configuration)).

---

## Contents
0. [Quick start — clone and run](#0-quick-start--clone-and-run)
1. [What you need](#1-what-you-need)
2. [The 3 things you transfer to the server](#2-the-3-things-you-transfer-to-the-server)
3. [Part A — On an internet-connected machine (build once)](#3-part-a--on-an-internet-connected-machine-build-once)
4. [Part B — On the air-gapped server (deploy)](#4-part-b--on-the-air-gapped-server-deploy)
5. [Part C — Load a manual](#5-part-c--load-a-manual)
6. [Part D — Use it](#6-part-d--use-it)
7. [Part E — The desktop guide (Phase 2)](#7-part-e--the-desktop-guide-phase-2)
8. [Everyday commands](#8-everyday-commands)
9. [Configuration](#9-configuration)

---

## 0. Quick start — clone and run

```bash
git clone https://github.com/shikharuniyal/software-guide.git
cd software-guide
```

This gets you the code only — the AI model weights (`models/`, ~6 GB) are **not**
in git and must be supplied separately (see [What you need](#1-what-you-need)).
Once `models/` is in place:

```bash
# 1. Install — build the images and start the stack
docker compose up -d --build

# 2. Use — load a manual, then ask it questions
docker compose run --rm ingestion /data/manuals/Word_manual.pdf
curl -X POST http://localhost:8000/admin/reload
# open http://localhost:8080 in a browser
```

That's the whole install-and-use flow on a machine **with internet and a GPU**.
Deploying to an **air-gapped Linux server** (no internet on the server itself)
needs one extra step — building the images elsewhere and transferring them
over — covered in full starting at [Part A](#3-part-a--on-an-internet-connected-machine-build-once).

---

## 1. What you need

- **One machine WITH internet** — to build the Docker images and gather the model
  weights (once). Call this the *build machine*.
- **The air-gapped server** — has Docker + a GPU, but no internet. This is where
  the system actually runs.
- The model weights folder `models/` (~6 GB). It is **not** in this git repo (too
  big) — you supply it once on the build machine, then transfer it.

> **Why this two-step dance?** An air-gapped server can't download Docker base
> images, Python packages, or model weights. So we prepare everything on a machine
> that *does* have internet, package it into files, and carry those files over.

---

## 2. The 3 things you transfer to the server

After Part A you will copy exactly three things to the server (USB / scp / rsync):

| # | What | Roughly |
|---|---|---|
| 1 | This project folder (`ongc-rag/`) | small |
| 2 | The `models/` folder (AI weights) | ~6 GB |
| 3 | The `images/` folder (Docker image tarballs) | ~10 GB |

---

## 3. Part A — On an internet-connected machine (build once)

```bash
# Get the project
git clone https://github.com/shikharuniyal/software-guide.git
cd software-guide

# Put the model weights in place (supplied separately, ~6 GB):
#   models/ollama/   -> qwen2.5vl + nomic-embed-text
#   models/hf/       -> yolox + table-transformer
# (this folder is git-ignored on purpose)

# 1. Build the project images + pull the two base images
docker compose build
docker pull ollama/ollama:latest
docker pull chromadb/chroma:latest

# 2. Save all 5 images to tarballs
mkdir -p images
docker save -o images/ollama.tar     ollama/ollama:latest
docker save -o images/chroma.tar     chromadb/chroma:latest
docker save -o images/api.tar        ongc-rag-api:latest
docker save -o images/frontend.tar   ongc-rag-frontend:latest
docker save -o images/ingestion.tar  ongc-rag-ingestion:latest
```

### Transfer to the server over SSH

Setup assumed here: **build machine = Windows**, **server = Linux**. Windows
ships an SSH client (`ssh`/`scp`) out of the box, so no extra install is needed.
Replace `user@server` with your login and IP, and `/opt/software-guide` with
wherever you want the project to live on the server.

**Windows (PowerShell) → Linux server, using `scp`:**

```powershell
# from inside the software-guide folder

# 1. create the destination folder on the server first
ssh user@server "mkdir -p /opt/software-guide"

# 2. project files (small) — everything except models/ and images/
scp -r client.py docker-compose.yml README.md `
    .env.example .gitignore manuals services `
    user@server:/opt/software-guide/

# 3. the model weights (~6 GB)
scp -r models user@server:/opt/software-guide/

# 4. the docker image tarballs (~10 GB)
scp -r images user@server:/opt/software-guide/
```

> `scp` has no resume/progress-per-file by default. For the big transfers (3–4
> above) you can add `-C` to compress in transit (`scp -rC ...`), which often
> helps on slower links.

**Linux / macOS / WSL → Linux server, using `rsync`** (resumable, shows
progress — nicer for the big folders if you have rsync available):

```bash
rsync -avP --exclude models --exclude images ./  user@server:/opt/software-guide/
rsync -avP models/   user@server:/opt/software-guide/models/
rsync -avP images/   user@server:/opt/software-guide/images/
```

---

## 4. Part B — On the air-gapped server (deploy)

SSH into the server, then run everything there:

```bash
ssh user@server
cd /opt/software-guide      # the folder you copied over (with models/ and images/ inside)

# 1. Load the 5 Docker images (no internet needed)
for f in images/*.tar; do docker load -i "$f"; done

# 2. Start the stack — NOTE: no --build, since images are already loaded
docker compose up -d
```

That's it — no config to edit. (Only if you *want* to change a port or model:
`cp .env.example .env` and edit it, then re-run `docker compose up -d`.)

Check it's alive:
```bash
docker compose ps
curl http://localhost:8000/health      # -> {"status":"ok"}
```

---

## 5. Part C — Load a manual

Do this once per manual (admin task). **The only value you change here is your
PDF's filename** — shown as `Word_manual.pdf` below.

```bash
# 1. Put the PDF in the manuals folder
cp /path/to/Word_manual.pdf manuals/

# 2. Build the search index from it  (<-- change the filename to yours)
docker compose run --rm ingestion /data/manuals/Word_manual.pdf

# 3. Tell the API to load the new index
curl -X POST http://localhost:8000/admin/reload
```

> Re-running steps 1–3 with a new PDF replaces the old manual.

---

## 6. Part D — Use it

Open a browser on (or pointed at) the server:

```
http://localhost:8080        # or  http://<server-ip>:8080
```

**Headless server (SSH only, no browser on it)?** Forward the ports to your own
laptop over SSH, then open the browser locally:

```bash
# run on your laptop; keep this session open
ssh -L 8080:localhost:8080 -L 8000:localhost:8000 user@server
# now open http://localhost:8080 in YOUR laptop's browser
```

Type a question, read the streamed answer + the workflow diagram.

Prefer the terminal?
```bash
curl -N -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How do I insert a table?"}'
```

---

## 7. Part E — The desktop guide (Phase 2)

The live on-screen guide runs on a **Windows** machine (not in Docker) and talks
to the server over the network.

```bash
# On the Windows machine (needs Python):
pip install mss opencv-python numpy requests uiautomation pyqt5 pynput

# Point it at the server (skip if running on the same machine):
set SERVER_URL=http://<server-ip>:8000/guide      # Windows CMD
# $env:SERVER_URL="http://<server-ip>:8000/guide"  # PowerShell

python client.py
# Type your goal, switch to the target app, and follow the highlighted boxes.
```

---

## 8. Everyday commands

```bash
docker compose ps                 # what's running
docker compose logs -f api        # follow the API logs
docker compose logs -f ollama     # follow the model server logs
docker compose restart api        # restart just the API

docker compose down               # stop everything (your data + index are KEPT)
docker compose up -d              # start again (index loads automatically)
docker compose down -v            # stop AND wipe the index (start from scratch)
```

> **Windows PowerShell note:** `curl` there is an alias that doesn't accept `-X`.
> Use `curl.exe -X POST ...` or `Invoke-RestMethod -Method Post -Uri ...`.

---

## 9. Configuration

All settings live in `.env` (copy `.env.example` to `.env`). You never edit the
Python code to change a model or a port.

| Setting | Default | Meaning |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5vl:7b` | the vision+language model (must be in `models/ollama`) |
| `EMBED_MODEL` | `nomic-embed-text` | the search embedding model |
| `WEB_PORT` | `8080` | the web page port |
| `API_PORT` | `8000` | the API port |
| `SERVER_URL` | `http://localhost:8000/guide` | where the desktop client (Phase 2) finds the server |

**Offline note:** the model weights are bundled in `models/` and mounted directly
into the containers, so nothing is ever downloaded at runtime. Building the images
(Part A) is the only step that needs internet, and it happens on a different
machine.
