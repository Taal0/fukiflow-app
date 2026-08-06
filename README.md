# FukiFlow App

Demo backend: SQLite catalog over batch_clean seed data + proxies to `fukiflow-vision` (typeset / compose / fonts / optional YOLO).

## Setup

```bash
cd fukiflow-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ../fukiflow-vision   # PageDoc / BubbleLayout types
```

Env (optional):

| Variable | Default | Meaning |
|---|---|---|
| `SEED_DIR` | `data/seed` | Imported chapter assets |
| `DB_PATH` | `data/fukiflow.db` | SQLite catalog |
| `VISION_URL` | `http://127.0.0.1:8001` | Light vision — typeset, compose, fonts, clean |
| `VISION_MODE` | `local` | `local` \| `remote` — which full YOLO worker to use |
| `VISION_FULL_URL` | `local` → same as `VISION_URL`; `remote` → **required** | Full vision — bubbles, order, pipeline |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Browser origins |

`VISION_MODE=remote` without `VISION_FULL_URL` fails at startup (no silent light fallback for YOLO).

## Seed

```bash
python scripts/seed.py "/path/to/test manga-translated v28"
# → chapter slug test-manga, cropped clean layers under data/seed/
```

## Run

### Letterer demo (light only)

```bash
# Terminal 1 — vision light (typeset + compose; no YOLO weights)
cd ../fukiflow-vision
VISION_LIGHT=1 .venv/bin/uvicorn app.main:app --port 8001

# Terminal 2 — app
cd ../fukiflow-app
.venv/bin/uvicorn app.main:app --port 8000 --reload
```

### Local YOLO test (Mac full vision)

Same machine: run vision **without** `VISION_LIGHT`, keep `VISION_MODE=local` (default). YOLO proxies hit the same `VISION_URL`.

```bash
# Terminal 1 — full vision (YOLO models required)
cd ../fukiflow-vision
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001

# Terminal 2 — app
cd ../fukiflow-app
VISION_MODE=local VISION_URL=http://127.0.0.1:8001 \
  .venv/bin/uvicorn app.main:app --port 8000 --reload
```

### Remote full worker (VPS light + Mac/VRAM YOLO)

VPS keeps light vision on `:8001`. Point YOLO at a tunnel or VRAM box — do **not** expose the Mac publicly.

```bash
# On Mac — full vision locally
cd fukiflow-vision
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001

# On Mac — SSH reverse tunnel to VPS (VPS :8002 → Mac :8001)
ssh -N -R 8002:127.0.0.1:8001 user@vps

# On VPS — app (systemd or shell)
VISION_MODE=remote \
VISION_URL=http://127.0.0.1:8001 \
VISION_FULL_URL=http://127.0.0.1:8002 \
  uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Later: same `VISION_MODE=remote` + `VISION_FULL_URL` aimed at a rented VRAM host; app code unchanged.

## API (UI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/chapters` · `/api/chapters/{slug}/pages` | Catalog |
| GET | `/api/pages/{id}` | PageDoc + asset URLs + cropped clean layers + `edit` / `edit_author` |
| GET/PUT/DELETE | `/api/pages/{id}/edit` | PageEdit overlay (`user_id` → `author` roller seam) |
| GET | `/api/chapters/{slug}/progress` | Per-page done_count / bubble_count |
| GET | `/api/fonts` · `/api/fonts/{key}/file` | Font catalog (+ file proxy) |
| POST | `/api/pages/{id}/typeset` | `{texts, font?, overrides?}` → layouts + text_layers |
| POST | `/api/pages/{id}/compose?download=0\|1` | `{doc, off?, font?}` → PNG |
| POST | `/api/chapters/{slug}/download` | `{pages:[{page_id,doc,off?,font?}]}` → zip |
| POST | `/api/vision/bubbles` | Full vision YOLO detect (+ order) |
| POST | `/api/vision/order` | Full vision reading order |
| POST | `/api/vision/pipeline` | Full vision detect → order → clean |
| POST | `/api/vision/clean` | Light vision inset-fill clean |

Translator / letterer write the same overlay field: `bubbles[<id>].text`.

## Desktop / packaging seams

Ship `fukiflow-app` + `fukiflow-vision` with `VISION_LIGHT=1` (typeset + compose only; no YOLO).

| Piece | Config |
|---|---|
| App data | `SEED_DIR`, `DB_PATH` (paths relative to package or absolute) |
| Vision | `VISION_URL` (light) · `VISION_MODE` · `VISION_FULL_URL` (YOLO when remote) |
| UI API | Same-origin `/api` **or** build with `VITE_API_BASE=http://127.0.0.1:8000` |
| Dev proxy | `VITE_DEV_PROXY_TARGET` (default `http://127.0.0.1:8000`) |

No machine-absolute paths are required at runtime: assets use `SEED_DIR`-relative paths, fonts resolve via catalog basename, chapter `source_dir` stores the batch folder name only.

## Smoke

```bash
curl -s localhost:8000/health | jq
curl -s localhost:8000/api/chapters | jq
curl -s localhost:8000/api/chapters/test-manga/pages | jq 'length'
curl -s localhost:8000/api/pages/1 | jq '.layer_assets | length'
curl -s localhost:8000/api/fonts | jq 'length'
curl -sI localhost:8000/api/fonts/comic_neue/file | head -5

# Full vision (VISION_MODE=local + Mac full, or remote tunnel up):
curl -s -F "file=@path/to/page.png" localhost:8000/api/vision/bubbles | jq '.bubbles | length'
```

UI: `cd ../fukiflow-ui && npm run dev` → http://127.0.0.1:5173
