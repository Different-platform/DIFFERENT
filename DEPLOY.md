# Deploying DIFFER

Three pieces, each on the host it needs:

| Piece | Host | Why there |
|---|---|---|
| API (`services/api`, FastAPI) | **Railway**, one persistent container (`Dockerfile`, `railway.json`) | Holds a ~420 MB embedding model in memory for the life of the process. Serverless (Vercel functions, Lambda) cannot do this. |
| Database (Postgres 16 + pgvector) | **Supabase** | Codebook, embeddings, survey microdata, conversations. |
| Web app | **Vercel** | Static frontend (`ui-template/`) or the Next.js proxy (`apps/web/`), see step 4. |

LLM calls go to **Groq** from the API only.

---

## 0. Before anything: things that must be true

1. **The database is reachable.** As of this writing `db.mnggojpjpqengdprlgoe.supabase.co` does not resolve at all (NXDOMAIN, also via 8.8.8.8 and 1.1.1.1). That usually means the Supabase project is **paused** (free projects pause after inactivity) or deleted. Open the Supabase dashboard:
   - **Paused:** restore it. The data comes back as it was.
   - **Deleted:** create a new project and rebuild the data (step 1b). The API cannot answer anything without it.
2. **Rotate the leaked credentials.** The Groq key and the Supabase database password were pasted into a chat in plaintext. Make new ones and use only the new ones below.
3. **Groq capacity.** The free tier allows 200,000 tokens per day; one question uses roughly 3,000-6,000. That is a few dozen questions a day for *everyone combined*. For real researchers, upgrade the Groq plan (Dev tier) before launch.

---

## 1. Database (Supabase)

**1a. Project restored (data intact):** nothing to do except copy the connection string.

Use the **direct connection or the session pooler (port 5432)**, not the transaction pooler (6543): the app uses server-side prepared statements, which the transaction pooler breaks.

**1b. Fresh project (data must be rebuilt):** from a machine that has the NHIS source files, with `DATABASE_URL` pointing at the new project:

```bash
.venv/bin/python db/run_migrations.py                       # schema + differ_ro role
for y in 2019 2020 2021 2022 2023; do
  .venv/bin/python -m jobs.ingest.run_ingest --year $y      # codebook: parse, embed, load
done
# survey microdata (one table per year)
.venv/bin/python - <<'EOF'
import psycopg
from services.api.config import settings
from jobs.ingest.survey_loader import load_survey_year, survey_csv_path
for y in (2019, 2020, 2021, 2022, 2023):
    with psycopg.connect(settings.database_url) as conn:
        print(load_survey_year(conn, y, survey_csv_path(settings.nhis_data_dir, y)))
EOF
```

Expected: 534 / 617 / 622 / 637 / 647 codebook variables and 31,997 / 31,568 / 29,482 / 27,651 / 29,522 survey rows for 2019...2023. The source CSVs and PDFs are not in git.

---

## 2. API (Railway)

```bash
railway login
railway init                 # or: railway link  (existing project)
railway up                   # builds the Dockerfile in this repo
```

Set these variables on the Railway service (**Variables** tab):

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase connection string (port 5432, new password) |
| `GROQ_API_KEY` | the new Groq key |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `EMBEDDING_MODEL` | `sentence-transformers/all-mpnet-base-v2` (must match the index) |
| `FRONTEND_ORIGIN` | the web app URL(s), comma-separated, e.g. `https://differ.vercel.app,https://differ.example.org` |
| `API_SHARED_SECRET` | a long random string (only if you deploy `apps/web`, see step 4) |
| `RATE_LIMIT_MAX`, `RATE_LIMIT_WINDOW_SECONDS` | optional, defaults 50 per 3600 s |

`PORT` is set by Railway; the container listens on it. `NHIS_DATA_DIR` is not needed by the API.

**Resources:** give it **at least 2 GB RAM**. The model and PyTorch take about 1 GB at rest.

**Check:** `https://<your-railway-domain>/api/health` should return `"status": "ok"`, `"llm": "groq"`, and `survey_years_loaded: [2019, ..., 2023]`. Startup takes about 30-60 s while the model loads; the health check allows 300 s.

**Keep one replica.** The rate limiter keeps its counters in memory; a second replica would split them.

---

## 3. Fix the Vercel "Deployment was blocked" check

Vercel blocked the preview builds with *"GitHub couldn't verify an account for the commit"*. Every commit on the branch was authored as `pukhraj@Pukhrajs-MacBook-Air.local`, which is not an email on any GitHub account.

- **Going forward:** set a real email, e.g. your GitHub no-reply address (GitHub Settings → Emails):
  `git config --global user.email "<id>+puks0618@users.noreply.github.com"`
- **Merging the PR through GitHub** creates the merge commit with your GitHub identity, so `main` is not affected by the old commits.
- If Vercel still blocks: Vercel project → Settings → Git, check the commit-author / team-member requirement.

---

## 4. Web app (Vercel)

Pick one. Both are in this repo.

### Option A, the polished UI (`ui-template/`), simplest

The browser calls the API directly.

- Vercel project → Settings → **Root Directory**: `ui-template` (`ui-template/vercel.json` handles the build and the `/chat` route rewrite).
- Environment variable (needed **at build time**): `VITE_API_BASE_URL = https://<your-railway-domain>` (no trailing slash).
- On Railway set `FRONTEND_ORIGIN` to this Vercel URL. Leave `API_SHARED_SECRET` **empty**: a browser cannot hold a secret, so with this option the API is open to anyone who finds its URL. The per-client rate limit is the only brake, and it counts per IP/session, not overall. Watch Groq usage.

### Option B, the Next.js proxy (`apps/web/`), the secure one

The browser only talks to Vercel; Vercel's server calls the API with the shared secret, so the API URL and secret never reach the browser.

- Root Directory: `apps/web`. Environment variables: `API_BASE_URL = https://<your-railway-domain>`, `API_SHARED_SECRET = <same value as on Railway>`.
- On Railway set the same `API_SHARED_SECRET`. Direct calls to `/api/chat` then get **401**.
- Trade-off: `apps/web` is a plain functional page, not the polished design. Porting the polished UI into it is the outstanding work to get both.

---

## 5. After deploying: verify

1. `/api/health` as in step 2.
2. In the web app, ask *"What is the average age of adults?"* (2023). Expected **48.10**.
3. Ask *"What's the weighted mean BMI for adults in 2023?"*, expected **28.03**, then *"Explain me in detail"*, then *"Can you do it categorically?"* (Obese 33.8%).
4. If you used Option B: `curl -X POST https://<railway>/api/chat -H 'Content-Type: application/json' -d '{"question":"hi"}'` should return **401**.

These numbers were recomputed independently from the raw NHIS CSV; any other value for the same question is wrong.

---

## Known limits at launch

- Survey years **2019-2023** only. 2017-2018 need the original NHIS codebooks with value-label tables, which were not available.
- Estimates are population-weighted **point estimates**; no standard errors or confidence intervals.
- No user accounts (by design for this version).
