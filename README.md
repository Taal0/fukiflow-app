# FukiFlow App

Demo backend: SQLite catalog over batch_clean seed data + proxies to `fukiflow-vision` (typeset / compose / fonts).

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
| `VISION_URL` | `http://127.0.0.1:8001` | fukiflow-vision base URL |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Browser origins |

## Seed

```bash
python scripts/seed.py "/path/to/test manga-translated v28"
# → chapter slug test-manga, cropped clean layers under data/seed/
```

## Run

```bash
# Terminal 1 — vision (light mode is enough for demo proxies)
cd ../fukiflow-vision
VISION_LIGHT=1 .venv/bin/uvicorn app.main:app --port 8001

# Terminal 2 — app
cd ../fukiflow-app
.venv/bin/uvicorn app.main:app --port 8000 --reload
```

## API (UI)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/chapters` · `/api/chapters/{slug}/pages` | Catalog |
| GET | `/api/pages/{id}` | PageDoc + asset URLs + cropped clean layers |
| GET | `/api/fonts` · `/api/fonts/{key}/file` | Font catalog (+ file proxy) |
| POST | `/api/pages/{id}/typeset` | `{texts, font?, overrides?}` → layouts + text_layers |
| POST | `/api/pages/{id}/compose?download=0\|1` | `{doc, off?, font?}` → PNG |
| POST | `/api/chapters/{slug}/download` | `{pages:[{page_id,doc,off?,font?}]}` → zip |

## Smoke

```bash
curl -s localhost:8000/api/chapters | jq
curl -s localhost:8000/api/chapters/test-manga/pages | jq 'length'
curl -s localhost:8000/api/pages/1 | jq '.layer_assets | length'
curl -s localhost:8000/api/fonts | jq 'length'
curl -sI localhost:8000/api/fonts/comic_neue/file | head -5
```

UI: `cd ../fukiflow-ui && npm run dev` → http://127.0.0.1:5173
