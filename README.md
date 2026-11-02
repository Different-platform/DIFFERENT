# DIFFER

DIFFER is a research assistant for the **National Health Interview Survey (NHIS)**, the
U.S. government's main household health survey. You ask a question in plain English and
get one of two things:

1. **What the survey measures.** "How is food insecurity measured?" gets a short answer
   that cites the exact codebook variable, section, page, and survey year. If NHIS does not
   ask about something ("What share of adults own a firearm?"), DIFFER says so plainly
   instead of guessing.
2. **Numbers from the survey data.** "What was the average BMI of U.S. adults in 2023?"
   gets a number computed from the real survey data, together with the exact SQL query
   that produced it.

Researchers otherwise search a 600-page codebook PDF by hand and write the queries
themselves. DIFFER does both and shows its working.

**Coverage:** NHIS Sample Adult public-use files, survey years **2019 to 2023**.

## The one rule: verified numbers only

DIFFER never shows a number it cannot back up.

- A language model writes the SQL query, but the query has to pass a checker before it runs.
  The checker confirms that every code in the query is a real answer code for that
  variable, and that "missing answer" codes (refused, don't know, and so on) are excluded
  for every column used.
- The query runs under a read-only database role, inside a read-only transaction, with a
  15-second time limit.
- If the check fails, you see the query and what was wrong with it, and no number.
- The sentence describing the result is written by code, not by the model, so it cannot
  contain a figure the database did not return.
- Follow-up answers ("explain that", "why did it go up?") are checked so that every
  number in them already appeared in the data shown. A reply that invents a number is
  rejected.

## Example

> **You:** What's the average BMI for adults in 2023?
>
> **DIFFER:** The average BMI for U.S. adults in 2023 was 28.03. BMI (body mass index)
> compares a person's weight with their height. It was worked out from the height and
> weight people reported. On the CDC's adult scale (under 18.5 underweight, 18.5 to 24.9
> healthy weight, 25 to 29.9 overweight, 30 or more obese), this average is in the
> overweight range. …
>
> *Shown above the answer: the variables used (`HEIGHTTC_A`, `WEIGHTLBTC_A`), the
> verified SQL, and the result table.*
>
> **You:** What is the reason for the increase in BMI from 2022 to 2023?
>
> **DIFFER:** The survey itself does not tell why the average BMI rose; it only records
> what people reported. In 2022 the average BMI was 27.96 and in 2023 it was 28.03, a very
> small increase that could simply be random variation …

## How it works

DIFFER is a **fixed pipeline**, not an autonomous agent. The steps always run in the same
order, which keeps answers consistent and makes evaluation results comparable from run to
run. No agent framework (LangChain, CrewAI, LangGraph) is used.

```
question
  ├─ follow-up about a number already shown?  → explain it / answer "why" or "how do I use it"
  │                                              from the saved result (no new query)
  ├─ rewrite      turn a follow-up into a standalone question, using the conversation
  ├─ route        is this about the codebook, the data, or both?
  ├─ year         year in the question > year selector > latest year (and say so)
  ├─ retrieve     find candidate variables (local embeddings + pgvector)
  ├─ judge        does NHIS measure this? MEASURED | RELATED | ABSENT
  └─ answer
       ABSENT    → fixed "not measured" reply, no citations, no number
       RELATED   → the closest variable, with a clear caveat
       MEASURED  → codebook answer with citations, or:
                   write SQL → check it → run it read-only → describe the result in words
```

| Step | Done by |
|---|---|
| Rewriting, routing, judging, writing SQL, writing prose | Language model (Groq, `openai/gpt-oss-120b`) |
| Retrieval, year choice, SQL checking, running the query, describing the result | Plain Python, no model |

Answers are written for readers without a statistics background. The variable codes, the
SQL, and the survey weight stay on the cards shown with each answer, so a researcher still
has everything needed to reproduce it.

## Repository layout

```
services/api/          FastAPI backend
  orchestrator.py      the pipeline
  rag/                 one module per step: rewrite, router, scope, retrieval, derivations,
                       judge, sql_gen, verify, present, generate, followup, explain,
                       guidance, style
  store/               database access (connection pool, vector search, read-only queries)
  llm/gateway.py       the only module that talks to Groq
  api/routes/          HTTP endpoints
  tests/               unit, property and integration tests
jobs/ingest/           codebook PDF parser, missing-code detection, embedding, loaders
db/migrations/         SQL schema (codebook, chat history and logs, read-only role)
eval/                  fixed question set and scoring harness
ui-template/           main web UI (Vite + React + Tailwind)
apps/web/              Next.js app with a server-side proxy that hides the API URL and secret
```

## Running it locally

### Requirements

- Python 3.12
- Node.js 20 or later
- A Postgres 16 database with the `pgvector` extension (hosted Supabase works; so does
  `docker compose up` with the included `docker-compose.yml`)
- A [Groq](https://console.groq.com) API key
- The NHIS source files (not in this repo): for each year, `adult-codebook-YYYY.pdf` and
  `adultYY.csv`, from the [NCHS NHIS data page](https://www.cdc.gov/nchs/nhis/)

### 1. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | What it is |
|---|---|
| `DATABASE_URL` | Postgres connection string. With Supabase, use the **direct** connection on port 5432; the 6543 pooler breaks prepared statements |
| `GROQ_API_KEY` | Your Groq key. Without it, the API uses a canned stub and says so in `/api/health` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `EMBEDDING_MODEL` | `sentence-transformers/all-mpnet-base-v2`. Must be the same at ingestion and at query time |
| `NHIS_DATA_DIR` | Folder holding the NHIS PDFs and CSVs |
| `RATE_LIMIT_MAX`, `RATE_LIMIT_WINDOW_SECONDS` | Requests allowed per client per window (default 50 per hour) |
| `FRONTEND_ORIGIN` | UI origin allowed by CORS, e.g. `http://localhost:5173` |

`.env` is gitignored. Never commit keys or passwords.

### 2. Install and set up the database

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python db/run_migrations.py                    # create tables and the read-only role
.venv/bin/python -m jobs.ingest.run_ingest --year 2023   # parse, check and load one codebook
```

Repeat the ingest for each year from 2019 to 2023 (`--dry-run` parses without writing).
The survey data tables (`survey_2019` … `survey_2023`) are loaded with
`load_survey_year(conn, year, csv_path)` in `jobs/ingest/survey_loader.py`. There is no
command-line wrapper for it yet.

### 3. Start the API and the UI

```bash
.venv/bin/uvicorn services.api.main:app --port 8100     # API on http://127.0.0.1:8100

cd ui-template && npm install && npm run dev            # UI on http://localhost:5173 (chat at /chat)
```

Use port **8100** for the API. Use `127.0.0.1` rather than `localhost` if something else
on your machine already listens on port 8000 or 8100.

Optional: the Next.js app in `apps/web` (`npm install && npm run dev`, port 3000) forwards
requests through its own server, so the browser never sees the API address or secret.
Configure it with `apps/web/.env.example`.

To try the UI without the API, start it with `VITE_USE_MOCK=true`. Mock mode shows a
banner, because its numbers are made up.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | Ask a question. Body: `{"question": "...", "conversation_id": "<uuid>", "year": 2023}`. Streams the answer as server-sent events: `route`, `verdict`, `citations`, `sql`, `result`, `token`, `error`, `done` |
| `GET /api/health` | Database status, model in use, which years are loaded |
| `GET /api/years` | Survey years available |
| `GET /api/variables/search?q=...` | Search codebook variables |
| `GET /api/variables/{year}/{name}` | One variable with its answer codes |

Send the same `conversation_id` (a UUID) with every message in a conversation, or
follow-up questions will not have their context. The API rejects an id that is not a valid UUID.

## Testing

```bash
.venv/bin/python -m pytest -q                      # unit and property tests; database tests skip without a DB
.venv/bin/python -m eval.harness run --check       # real pipeline on the fixed question set, compared with eval/baseline.json
.venv/bin/python -m eval.harness run --save-baseline   # only after a clean run
```

The eval harness runs the questions in `eval/questions.json` through the real pipeline,
including multi-turn conversations. It scores the verdict, whether the SQL was verified,
whether a number was produced when it should be (and not when it shouldn't), and the
wording. It is paced to stay under Groq's free-tier limits and takes about five minutes.

## Limitations

- **Point estimates only.** Results are weighted by the survey's final adult weight
  (`WTFA_A`), but no margin of error is computed: the survey design variables (`PSTRAT`,
  `PPSU`) are not used yet. To publish a margin of error or test a difference, re-run the
  estimate in survey software (R `survey`, Stata `svy`, SAS `PROC SURVEYMEANS`).
- **BMI comes from self-reports.** NHIS has no continuous BMI variable, so DIFFER computes
  it from reported height and weight (703 × pounds / inches²). Self-reported BMI usually
  runs lower than measured BMI.
- **2019 to 2023 only.** Earlier years used a different questionnaire. Their codebook
  files do not include answer-code tables, which the SQL check depends on.
- **Range filters are rejected.** The SQL check currently rejects filters like
  `AGEP_A >= 18` on continuous variables. Queries use "exclude the missing codes" instead.
- **RELATED vs ABSENT is a judgement call** by the model, and can differ between runs on
  borderline wording.
- **Groq free tier.** About 200,000 tokens a day for `openai/gpt-oss-120b`. A day of testing
  plus one eval run can use it up. When that happens the UI says so; it does not guess.
- **No login.** The API has rate limiting but no authentication. Run it on localhost or
  behind the `apps/web` proxy.

## Data source

National Center for Health Statistics. National Health Interview Survey, 2019–2023,
Sample Adult public-use files. Public-use data; source files are not redistributed in
this repository.
