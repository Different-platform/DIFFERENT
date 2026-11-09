# DIFFER — context for a new chat

Paste this whole file at the start of a new chat. It is the single source of truth for what exists,
what works, what is unverified, and what to watch out for. It supersedes the status section at the
bottom of `PROJECT_HANDOFF.md`, which describes an earlier point.

Written against commit `2477b44` on branch `feature/api` (pushed to GitHub).
No secrets are in this file. Credentials live in `DIFFERENT/.env` (gitignored); only variable names appear below.

---

## 1. What DIFFER is

A research assistant over the **National Health Interview Survey (NHIS) Sample Adult public-use data**,
for health researchers who otherwise hand-search a ~640-page codebook PDF and hand-write SQL/Stata.
Two capabilities:

1. **Codebook Q&A with citations.** Plain-language question → grounded answer citing variable, section,
   codebook page, survey year; or an honest "NHIS does not measure this".
2. **Verified descriptive statistics.** Population-weighted means, shares, distributions, breakdowns.
   SQL is generated, **verified against the codebook before it runs**, executed read-only, and returned
   with the exact SQL. **If verification fails, the user sees the SQL and the problems and no number.**

**Product promise: verified statistics only.** Never weaken this to make an answer look more helpful.

Coverage that actually exists: **survey years 2019–2023**. 2017–2018 are in the spec but are **not built** (section 9).

---

## 2. Where everything lives (the user's machine, macOS, user `pukhraj`)

| Thing | Path |
|---|---|
| Git repo (all code) | `/Users/pukhraj/Different/DIFFERENT` → GitHub `Different-platform/DIFFERENT` |
| Spec (authoritative) | `/Users/pukhraj/Different/.kiro/specs/differ-platform/{requirements,design,tasks}.md` — **outside the git repo** |
| Architecture drafts, UI prompt set | `/Users/pukhraj/Different/` (`DIFFER_*Architecture*.md`, `00-…04-*.md`) — historical |
| Source data (NOT in git) | `/Users/pukhraj/Downloads/Different Platform Project/` (set as `NHIS_DATA_DIR`) |
| Secrets | `DIFFERENT/.env` (gitignored) |
| Python venv | `DIFFERENT/.venv` (Python 3.12) |

- Branch `feature/api` is **15 commits ahead of `main`**; **PR #1 is open** (`feature/api → main`), awaiting the user's review/merge.
- Other remote branches: `feature/backend-api` (older ancestor of `feature/api`), `fix/ingest-parser-2019-2023` (not examined).
- `tasks.md` still shows checkboxes unticked (it is outside the repo and edits to it were lost). Do not trust it for progress.
- `trace_pipeline.py` in the repo root is an **untracked throwaway debug script**; never commit it.

---

## 3. Architecture in one picture

A **fixed pipeline with LLM decision points** — deliberately *not* an agent. No LangChain/CrewAI/LangGraph
(the spec forbids them; a fixed flow keeps eval results comparable).

```
question (+ conversation_id, optional year)
  │  load history  → save user message
  ├─ if last turn was a computed statistic AND this message asks to explain it
  │     → rag/followup.py (LLM classify) → rag/explain.py (answer from saved SQL; numbers checked)  ── done
  ├─ rag/rewrite.py     follow-up → standalone question (uses history)
  ├─ rag/router.py      intent: codebook | data | both
  ├─ rag/scope.py       choose year: year in question > selector > (data only) latest year, flagged
  ├─ retrieval          local embeddings + pgvector (year-filtered); design/weight vars excluded
  │     └ rag/derivations.py adds inputs of known derived measures (BMI → height + weight)
  ├─ verdict            derivation rule if all inputs present; else rag/judge.py (LLM): MEASURED | RELATED | ABSENT
  └─ branch
       ABSENT   → fixed refusal text, no citations, no number
       RELATED  → closest variable + caveat (LLM prose)
       MEASURED + codebook → grounded prose with citations (LLM stream)
       MEASURED + data/both →
            rag/sql_gen.py (LLM; real codes injected)  ≤2 attempts (1 repair with verifier feedback)
            rag/verify.py  (pure Python gate)          ← nothing runs unless this returns no problems
            store/readonly_store.py                    ← executes under role differ_ro, READ ONLY txn, 15 s timeout
            rag/present.py (pure Python)               ← labels codes, formats %, writes the summary
```

**What the LLM does vs. what is deterministic**
- LLM (Groq, `openai/gpt-oss-120b`): rewrite, route, judge, SQL generation, codebook prose, explain-follow-up classify + prose.
- Deterministic: retrieval, year choice, derivation rule, **SQL verification**, execution, result labelling and the stats summary text. The summary is copied from the verified result; it cannot contain a figure the DB did not produce.

**Safety gates (do not simplify away)**
1. Per-variable sentinels (missing codes) — never a global "7/8/9" rule. Known fixture: `HISPALLP_A` code 7 = real category "Other single and multiple races"; 97/98/99 are sentinels.
2. Presence judge (LLM reasoning), because vector similarity cannot detect absence.
3. `verify.check()`: every numeric code compared against a column must exist in that variable's value labels; only a single read-only SELECT; and **every column the query uses must have its missing-data codes excluded** (added this session — a mean over raw weights including 999 used to pass).
4. `differ_ro` role + READ ONLY transaction as a second, independent barrier.
5. Explain path: any number in the explanation that isn't in the saved material triggers a retry, then a fallback to the already-shown summary.

---

## 4. Data and database (Supabase Postgres 16 + pgvector 0.8.2)

Migrations applied (`db/migrations`, run with `db/run_migrations.py`): `001_codebook`, `002_telemetry`, `003_differ_ro`.

| Table | Contents |
|---|---|
| `codebook_variables`, `value_labels`, `sections`, `variable_availability` | parsed codebook: 534 / 617 / 622 / 637 / 647 variables for 2019…2023, embedded (768-dim, HNSW), per-variable `is_sentinel` |
| `survey_2019` … `survey_2023` | microdata, one table per year (31,997 / 31,568 / 29,482 / 27,651 / 29,522 rows). Created at load time from each CSV header (`jobs/ingest/survey_loader.py`); column types inferred (numeric, a few text id columns like `hhx`); `SELECT` granted to `differ_ro` |
| `conversations`, `messages`, `query_log` | chat history (`messages` stores `generated_sql`/`sql_verified` so "explain that" works), telemetry. `conversations.user_id` is nullable, reserved for future auth |

Source files per year: `NHIS_adult_YYYY/adult-codebook-YYYY.pdf` + `adultYY.csv` (2019–2023).

Retrieval detail that mattered: `vector_store._enable_iterative_scan` sets `hnsw.iterative_scan='strict_order'`. Without it a year-filtered query returned 6–7 of 10 requested rows and dropped relevant variables.

`NON_SUBSTANTIVE_VARIABLES` (weights, PSTRAT, PPSU, HHX, RECTYPE, SRVY_YR) are excluded from retrieval but remain usable in generated SQL.

---

## 5. How to run it

**Ports — important:** API **8100**, Vite UI **5173**, Next.js app **3000**. Port **8000 is NOT ours**: a Chroma
Docker container (`chromadb/chroma`) publishes it on IPv4 and IPv6. Use `127.0.0.1`, not `localhost`.

```bash
cd /Users/pukhraj/Different/DIFFERENT
.venv/bin/uvicorn services.api.main:app --port 8100       # API (no --reload; restart after code changes)
cd ui-template && npm run dev                              # polished UI → http://localhost:5173  (console at /chat)
cd apps/web && npm run dev                                 # Next.js app + secrets-hiding proxy → :3000

.venv/bin/python -m pytest -q                              # 225 tests; DB-backed ones skip if no DB reachable
.venv/bin/python -m eval.harness run --check               # real pipeline, ~5 min, paced; compares to eval/baseline.json
.venv/bin/python -m eval.harness run --save-baseline       # only after a clean, valid run
.venv/bin/python db/run_migrations.py [--check]
.venv/bin/python -m jobs.ingest.run_ingest --year 2023 [--dry-run]   # codebook ingest (parse → abort guard → embed → load)
```
There is no CLI for the survey-table loader; it was called as `load_survey_year(conn, year, csv_path)` from `jobs/ingest/survey_loader.py`.

**Environment variables** (names only, in `DIFFERENT/.env`): `DATABASE_URL`, `GROQ_API_KEY`, `GROQ_MODEL`, `EMBEDDING_MODEL`,
`NHIS_DATA_DIR`, `RATE_LIMIT_MAX`, `RATE_LIMIT_WINDOW_SECONDS`, `FRONTEND_ORIGIN`. `apps/web/.env.example`: `API_BASE_URL`, `API_SHARED_SECRET`.
- `DATABASE_URL` is hosted Supabase, **direct** connection on 5432 (psycopg3 prepared statements break the 6543 pooler).
- Docker is not required (Supabase is hosted); `docker-compose.yml` exists for a local pgvector DB but isn't used.

---

## 6. Frontends — two exist, know which is which

| | `ui-template/` (Vite + React 19 + Tailwind v4 + Radix) | `apps/web/` (Next.js 15) |
|---|---|---|
| Purpose | The **polished UI the user actually uses** (landing `/`, console `/chat`) | Spec-required production shape: **server-side proxy hides the backend URL/secrets** |
| Talks to API | directly at `http://127.0.0.1:8100` (CORS-allowed) | via same-origin `/api/chat`, `/api/health` route handlers |
| State | wired to the real API; conversation memory works | plain functional page, **not** a port of the polished design |

- `ui-template/src/lib/differ/ask.ts` is the switch: real API by default; `VITE_USE_MOCK=true` → canned mock data. **Mock mode shows a visible banner** ("made-up numbers") because mock answers (e.g. "BMI_A … 28.47") were once mistaken for real results.
- **Conversation memory depends on the UI sending a real UUID `conversation_id` on every question** (`crypto.randomUUID()` per thread in `console-page.tsx`). Without it every message starts fresh. The API rejects a non-UUID id with HTTP 422 on purpose.
- Year selector offers 2019–2023 only.
- The "x of 50 requests this hour" in the UI is a local cosmetic counter. The real limit is server-side (`RATE_LIMIT_MAX`, per IP or `X-Session-Id`).
- `ask.ts` / `api-stream.ts` are original project files — **do not delete them as "stray"** (an earlier mistake that broke memory).

---

## 7. What was built and verified (chronological, condensed)

1. Config/env repaired; Groq model changed from the decommissioned `llama-3.3-70b-versatile` to **`openai/gpt-oss-120b`**.
2. **Verified statistics path**: `sql_gen`, `verify`, `readonly_store`, `analysis/descriptive.py`, `003_differ_ro.sql`, `survey_loader`; survey microdata for 2019–2023 loaded.
3. Retrieval fix: weights/design vars no longer pollute candidates.
4. **Eval harness** (`eval/`): fixed questions scored on verdict, `sql_verified`, produced-a-number, wording; supports multi-turn threads via a `conversation` key.
5. Next.js app with secrets-hiding proxy (verified: client bundle contains no backend URL or secret).
6. "Answers are just labels / forgets follow-ups" fixes: result labelling and percent formatting (`present.py`), derived measures (BMI), year resolution (`scope.py`), missing-data exclusion check in the verifier, one SQL repair attempt, citations = variables the SQL actually used, empty result ≠ a number, conversation UUID in the UI, history read before saving the new message, follow-up-aware rewriter, year-filtered recall fix.
7. "Explain me in detail" now answers from the saved computation (`followup.py`, `explain.py`); BMI verdict decided by rule not by the judge (the same question used to flip between MEASURED and "no BMI exists" run to run; now 5/5 identical).
8. Port 8000 → 8100 (Chroma collision), CORS for both loopback spellings, honest rate-limit errors (`LLMRateLimitError`).

**Independently verified numbers** (recomputed from the raw CSV with no DB or pipeline): 2023 weighted mean BMI **28.03**
(n=26,969 with valid height+weight), 2023 share obese (`BMICAT_A=4`) **33.8%**, 2023 mean age **48.10**, 2021 mean age **48.12**, 2021 mean BMI **27.9**.
Use these as sanity anchors; any other number shown for the same question is wrong.

---

## 8. Verification status — be precise about what is and isn't proven

| Claim | Status |
|---|---|
| 225 unit/property/integration tests pass | last full run passed |
| Eval 13/13 (original 9 + obese share + 3-turn BMI thread) | **valid**, run before the explain path existed |
| Eval with the 2 new explain cases (`e1`,`e2`) → 15 questions | **never completed validly** — Groq daily quota ran out mid-run (5/15 was quota failures, not quality). `eval/baseline.json` still reflects the **13-question** run |
| BMI thread end to end (mean → explain → categorical → "what about 2021") | worked via API on the pre-port-change code; **not re-run since**, quota spent |
| "How is food insecurity measured?" via `localhost:8100` with the UI's Origin | worked |
| Anything clicked through in a real browser by the assistant | **never** — only API-level and served-source checks. The user saw the old port error in their browser, which the port fix addresses; the user has not confirmed since |

---

## 9. Known issues, limits, and traps

**Groq free tier (this org):** 8,000 tokens/min and **200,000 tokens/day** for `openai/gpt-oss-120b`. A day of testing plus one eval exhausts it. When spent, the UI says
"The AI service's daily usage limit has been reached… try again in about N minutes". The gateway retries 429s up to 6 times (right for the per-minute cap, pointless for the daily one). A paid tier or per-stage smaller models (router/rewrite/follow-up classify don't need 120B) would fix it.

**File reverts — the biggest practical trap.** The user's environment periodically reverts files the assistant edited (a banner like "N files reverted"). Uncommitted work vanished repeatedly. Therefore:
- **Commit and push every change immediately.** Recover with `git diff > /tmp/x.patch && git checkout -- .`.
- After a revert, the **running backend still holds the new code in memory** (no `--reload`) while Vite **hot-reloads the old UI**. Always compare disk with `git status`/`git diff` before claiming a fix is live, and tell the user to hard-refresh (Cmd+Shift+R).
- Ask before undoing a revert the user may have meant; if they are reporting the exact bug the reverted code fixes, restoring from the pushed commit is the right call (back up the diff first).

**Secrets:** the Groq key and Supabase password were pasted into chat in plaintext. **Rotate both.** `.env` is gitignored and was never committed.

**Statistical limits to tell researchers:** estimates are population-weighted **point estimates only** — no standard errors/confidence intervals (design variables `PSTRAT`/`PPSU` are not used). BMI is derived from self-reported height/weight by formula (703 × lb / in²), not an NHIS-published variable.

**Verifier limitation:** the "unknown code" check treats a continuous variable's only defined codes as its sentinels, so a range filter like `AGEP_A >= 18` is flagged. The SQL prompt tells the model to use only `NOT IN (sentinels)`; fine today, but a real false-positive source.

**Smaller things:** RELATED vs ABSENT is a soft judgement (the eval accepts either); the judge can still flip on borderline wording for non-derived concepts; eval rows in `query_log` are not tagged with a `strategy`; no CI exists (no static import-graph / dependency checks); `survey_YYYY` tables are built at load time, not by a migration.

---

## 10. Not done / suggested next steps (priority order)

1. **Re-verify after the Groq quota resets:** run the BMI thread in the real browser, then `eval.harness run --check`; if 15/15, `--save-baseline`.
2. **Rotate the leaked credentials.**
3. **Merge PR #1** (user's decision) and delete/reconcile `feature/backend-api`, check `fix/ingest-parser-2019-2023`.
4. **Port the polished UI into `apps/web/`** (Next.js) so production has both the design and the secrets-hiding proxy; today the polished UI calls the API directly (fine for local dev only).
5. **Deployment config** (Railway persistent container for the API — the ~420 MB embedding model must stay resident; Vercel for the web app).
6. **2017–2018 legacy track** — *blocked on data*: the provided PDFs (`samadult_summary_YYYY.pdf`) are record-layout summaries with **no value-label tables**, so sentinels and SQL verification cannot be built from them. Needs the real pre-2019 codebooks. Do not build a hollow parser.
7. Cut Groq token use (smaller model for router/rewrite/follow-up classify); consider a paid tier.
8. Standard errors using `PSTRAT`/`PPSU`; relax the verifier for range filters on continuous variables.
9. Reconcile `tasks.md`; add the CI static checks listed in the spec; tag eval `query_log` rows.

---

## 11. Key files map

```
services/api/
  main.py              app, middleware order (CORS outermost), allowed_origins()
  orchestrator.py      the fixed pipeline; _verified_statistics, _explain_previous, _with_derivation_inputs
  config.py            pydantic Settings (reads DIFFERENT/.env)
  rag/                 rewrite router scope retrieval derivations judge sql_gen verify present generate followup explain
  store/               db (pool) vector_store relational_store readonly_store
  llm/gateway.py       the ONLY importer of the Groq SDK; LLMRateLimitError; FakeLLMGateway for tests
  llm/stub_policy.py   canned replies when no GROQ_API_KEY
  middleware/          rate_limit, timing
  tests/               15 test modules incl. test_cors, test_verify, test_present, test_explain, test_orchestrator
jobs/ingest/           codebook parser (2019+ only), sentinels.py (SAFETY-CRITICAL), abort_guard, embedder, loader, survey_loader
eval/                  harness.py, questions.json (15), baseline.json (13-question run)
db/migrations/         001_codebook 002_telemetry 003_differ_ro      db/run_migrations.py
ui-template/           polished Vite UI (see section 6)               apps/web/   Next.js app + proxy
PROJECT_HANDOFF.md     original handoff (partly stale)                CONTEXT_FOR_NEW_CHAT.md   this file
```

---

## 12. How to work with this user (learned)

- They test in a real browser and send screenshots; a screenshot beats any amount of API-level evidence. Take them seriously — twice "it feels fishy" was correct (mock data shown as real; a model flip-flopping on an identical question).
- They want things done, not proposed ("do it for me"). But they also revert changes, so state plainly what is on disk vs. in git.
- Verify claims with real runs and independent recomputation (raw CSV), and say explicitly what was *not* verified.
- Say when a failure is the environment (quota, port, revert) rather than the code.
