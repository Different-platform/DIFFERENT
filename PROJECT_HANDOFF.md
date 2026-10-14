# DIFFER — Project Handoff & Onboarding

**Purpose of this file:** a single, self-contained briefing on the DIFFER platform — what it is,
why it is designed the way it is, exactly what has been built so far, and what needs to happen
next. Written so that both a new human contributor and an AI assistant (Claude, Kiro, etc.) can
read this one file and have accurate, complete context before touching code.

**Status as of 12 October 2026**

| | |
|---|---|
| Repo | `github.com/Different-platform/DIFFERENT` |
| Branch | `main` (only branch) |
| Commits | 1 — `7157bce` "Add DIFFER frontend UI template (mock-data driven)" |
| Code written | Frontend UI template only (~1,900 lines TS/TSX). **Zero backend code.** |
| Spec | Complete and settled (requirements + design + 21-task plan), **0 of 21 tasks started** |
| Team | 2 contributors, onboarding a 3rd |

---

## Table of contents

1. [What DIFFER is](#1-what-differ-is)
2. [Why it exists — the three findings that shaped the design](#2-why-it-exists--the-three-findings-that-shaped-the-design)
3. [Document map — what is authoritative](#3-document-map--what-is-authoritative)
4. [Current state of the codebase](#4-current-state-of-the-codebase)
5. [Settled architecture](#5-settled-architecture)
6. [The fixed pipeline](#6-the-fixed-pipeline)
7. [Data model](#7-data-model)
8. [The four safety-critical mechanisms](#8-the-four-safety-critical-mechanisms)
9. [Ingestion design](#9-ingestion-design)
10. [Testing strategy](#10-testing-strategy)
11. [What is explicitly out of scope](#11-what-is-explicitly-out-of-scope)
12. [Known discrepancies and gaps — read before coding](#12-known-discrepancies-and-gaps--read-before-coding)
13. [Next steps — the build order](#13-next-steps--the-build-order)
14. [Open decisions that block work](#14-open-decisions-that-block-work)
15. [Future roadmap (v2 and beyond)](#15-future-roadmap-v2-and-beyond)
16. [How to run what exists today](#16-how-to-run-what-exists-today)
17. [Working conventions](#17-working-conventions)
18. [Glossary](#18-glossary)

---

## 1. What DIFFER is

DIFFER is a research assistant for the **National Health Interview Survey (NHIS)** public-use
data. The users are health and human-science professors and researchers — people who currently
work by hand-searching a ~640-page codebook PDF and then hand-writing SQL or Stata/R code
against a ~30,000-row × ~640-column CSV per survey year.

This version ships exactly **two capabilities**:

**1. Codebook Q&A with citations.** A researcher asks a plain-language question
("How is food insecurity measured?"). The system retrieves candidate codebook variables, judges
whether NHIS actually measures the concept asked about, and returns a grounded answer citing
**variable name, section, codebook page, and survey year** — including an explicit, honest
"NHIS does not measure this" when that is the truth.

**2. Verified descriptive statistics.** A researcher asks for a weighted mean or crosstab. The
system generates SQL using the variable's *actual* value-label codes, **verifies that SQL against
the parsed codebook before any number is shown**, executes it under a read-only database role,
and returns the result alongside the exact SQL that produced it.

Coverage target: **survey years 2017–2023, all variables** (not a curated subset).

Scale target: **30–40 concurrent researchers** on a single always-on backend container. Each
question triggers several sequential LLM calls (rewrite/route → presence judge → SQL generation →
answer generation).

### The product promise

> **Verified statistics only.** If a generated query fails verification, the user sees the query
> and the problems with it, and **no number** — not in a table, not in the prose.

This single rule is the reason the project exists. It is enforced in the UI template already and
must be enforced in the backend orchestrator.

---

## 2. Why it exists — the three findings that shaped the design

These came out of an exploratory prototype before the spec was written. Every significant design
decision traces back to one of them. Do not "simplify away" the mechanisms they justify.

### Finding 1 — Sentinel codes are per-variable, never global

NHIS encodes missing/refused/unknown answers as numeric sentinel values. The naive assumption is
that `7`, `8`, `9` (or `97/98/99`) always mean "refused / not ascertained / don't know".

**That assumption silently deletes real data.** On `HISPALLP_A` (Hispanic origin + race), code
`7` means **"Other single and multiple races"** — a real racial category — while `97/98/99` are
the sentinels on that same variable. A blanket "exclude 7, 8, 9" rule erases a racial group from
every analysis, and the result still looks plausible.

Sentinel families observed across 2019–2023: `7/8/9`, `97/98/99`, `997/998/999`,
`9997/9998/9999`, `99997/99998/99999`.

→ **Design consequence:** `is_sentinel` is computed and stored **per `(variable, code)` pair**,
derived only from that variable's own codebook entry. There is no global sentinel rule anywhere
in the codebase. `HISPALLP_A` is a hard-coded regression test fixture.

### Finding 2 — Vector similarity cannot detect absence

A cosine-similarity threshold cannot tell you whether NHIS measures a concept. Measured and
unmeasured concepts **overlap completely** in the score distribution — in testing, a genuinely
measured concept scored *lower* than every absent one. Similarity measures topical relatedness,
not whether the retrieved item *is* the thing you asked about.

Adding an explicit LLM call that reasons about **construct equivalence** took absence detection
from **0/4 to 4/4**.

→ **Design consequence:** the `Presence_Judge` stage is mandatory and returns one of three
verdicts: `MEASURED` / `RELATED` / `ABSENT`. It is an LLM reasoning call, never a score threshold.

### Finding 3 — Given thin context, the LLM invents sentinel codes

Asked to write SQL excluding missing values, the model produced `NOT IN (77, 88, 99)` when the
real codes were `7, 8, 9` — and returned a confident, plausible-looking `4.298%`.

→ **Design consequence:** two gates. The SQL generator gets the real codes **injected into its
prompt context** (never left to model recall), and a separate `SQL_Verifier` **re-checks every
numeric code in the generated SQL against the stored value labels before execution**. A single
mismatch means no number reaches the user.

### Bonus finding — silent parse failure

During ingestion testing, **three of five survey years silently parsed to zero variables** and
the pipeline carried on, happily serving confident wrong answers from an empty index.

→ **Design consequence:** an **abort guard**. If parsed variable count for a year falls below
threshold, that year's load aborts entirely — zero rows written — and the job reports which year
and what count triggered it.

---

## 3. Document map — what is authoritative

Files live in two places. **The spec is the authority; the architecture drafts are history.**

### Authoritative (in `.kiro/specs/differ-platform/`, at the workspace root)

| File | Contents |
|---|---|
| `requirements.md` | 20 numbered requirements in EARS format, a glossary, and the Out-of-Scope list. **This is the contract.** Where any two documents disagree, this one wins. |
| `design.md` | Full technical design: deployment topology, module layout, interface sketches, complete SQL schema, ingestion design, error-handling table, **14 correctness properties**, testing strategy. |
| `tasks.md` | 21 top-level tasks / ~80 subtasks, each traced to a requirement, with a dependency-wave graph. All currently unchecked. |

### Historical drafts (workspace root) — useful for *why*, not for *what*

| File | Contents |
|---|---|
| `DIFFER_Platform_Architecture.md` | First architecture draft (563 lines). Best source for the prototype findings in §2 above and the two-safety-gates reasoning. |
| `DIFFER_Architecture_v2.md` | Second draft (821 lines). Adds DiD/harmonization design, 6-person team→track mapping, variable dependency graph. **Much of it is deliberately out of scope now** — see §11. |
| `Architecture_Draft_Prajwal.pdf`, `Markdown to PDF (3).pdf` | PDF exports of the above. |

### Frontend spec (workspace root) — these were the prompt set used to build the UI template

| File | Contents |
|---|---|
| `00-kickoff-prompt.md` | The kickoff instruction given to the agent |
| `01-ui-requirements.md` | Routes, layout, conversation flow, 6 required demo states, acceptance checklist |
| `02-ui-design.md` | Visual direction, typography, the full oklch color-token set, component table, streaming timings |
| `03-mock-data.md` | Verbatim sample conversations, citations, SQL, results; TS types; streaming event contract |
| `04-tasks.md` | The 9-step build order that was followed |

**Reconciliation table — where the drafts were overridden by `requirements.md`:**

| Topic | Draft said | Settled decision |
|---|---|---|
| Backend host | Render / Railway / Fly | **Railway** (persistent container) |
| Database host | Supabase *or* Neon | **Supabase, paid tier** |
| Auth | Supabase Auth, magic link + domain allowlist | **No auth at all in v1.** Domain allowlist is *invalid* for this product — users span gmail/.edu/etc. Schema reserves a nullable `user_id`. |
| Variable scope | ~120 curated variables (to fit a free tier) | **All variables, 2017–2023** |
| DiD / harmonization | v2 feature, schema in v1 | **Out of scope entirely — not even the schema** |
| Pre-2019 years | Open decision | **In scope**, via a dedicated isolated parser + crosswalk |
| Team structure | 6 tracks, 1 owner each | Organizational convention only; team is 2–3 people |

---

## 4. Current state of the codebase

```
DIFFERENT/                          ← the git repo
├── PROJECT_HANDOFF.md              ← this file
└── ui-template/                    ← the ONLY code that exists
    ├── package.json                Vite 8 + React 19 + TS 6 + Tailwind v4 + Radix
    ├── index.html                  IBM Plex Sans/Mono via <link>
    ├── .oxlintrc.json              oxlint (not eslint)
    ├── vite.config.ts              @ alias → src/
    ├── public/{favicon.svg,icons.svg}
    └── src/
        ├── App.tsx                 react-router-dom: "/" → Landing, "/chat" → Console
        ├── main.tsx
        ├── index.css               Tailwind v4 @theme block with the oklch design tokens
        ├── lib/
        │   ├── utils.ts            cn() helper
        │   └── differ/             ← THE SWAP POINT FOR A REAL API
        │       ├── types.ts        Verdict, RouteIntent, Citation, SqlResult,
        │       │                   NumericResult, Message, Conversation, SURVEY_YEARS
        │       ├── mock-data.ts    6 canned demo conversations (274 lines)
        │       ├── mock-stream.ts  StreamEvent union + mockAsk() async generator (209 lines)
        │       └── format.ts       relative-date helper
        ├── components/
        │   ├── ui/                 shadcn-style primitives: badge, button, card,
        │   │                       dropdown-menu, select, sheet, textarea, tooltip
        │   └── differ/             route-indicator, verdict-badge, citation-card,
        │                           sql-panel, result-card, year-selector,
        │                           rate-limit-indicator, message-bubble,
        │                           conversation-sidebar, empty-state
        └── pages/
            ├── landing-page.tsx    hero + 3 feature cards + footer
            └── console-page.tsx    owns all state: conversations, streaming,
                                    rate limit, States menu, mobile Sheet (301 lines)
```

**Verified:** `npm run build` (`tsc -b && vite build`) passes clean. 2,005 modules, 428 kB JS
bundle, built in ~150 ms.

### What the UI template actually does

Everything is driven by local mock data and a simulated stream. There is no network call anywhere.

- **Two routes.** Landing (`/`) with a CTA to the console (`/chat`).
- **Console shell.** Header with DIFFER wordmark, year selector (All years + 2017–2023, stored as
  `default_year: number | null`), "New conversation" button. 280 px sidebar listing conversations
  with relative dates and active highlight; collapses to a Radix Sheet below 1024 px.
- **Fixed assistant artifact order**, enforced in `message-bubble.tsx`:
  `route indicator → verdict badge → citation cards → SQL panel → result table → streamed text`.
- **Simulated streaming.** `mockAsk()` is an async generator yielding
  `route → verdict → citations → sql → result → token* → done` with the documented timings
  (route ~1.1 s, ~350 ms artifact gaps, SQL→result ~600 ms, ~22 ms per token). Tokens are split on
  whitespace and appended with a typing cursor.
- **Six demo conversations**, all reachable from the sidebar and from a "States" dropdown:

  | id | Title | Demonstrates |
  |---|---|---|
  | `c1` | How is food insecurity measured? | `MEASURED`, route `codebook`, 2 expandable citations |
  | `c2` | Weighted mean BMI, 2023 | `MEASURED`, route `both`, **verified** SQL + single-row result |
  | `c3` | Hispanic origin breakdown | `MEASURED`, route `data`, **FAILED verification** — SQL shown, problems listed, **no number anywhere** |
  | `c4` | Loneliness measures | `RELATED` — closest variable `SUPPORT_A` + explanation of how it differs |
  | `c5` | Firearm ownership | `ABSENT` — not measured, no citations |
  | `c6` | Uninsured rate by sex, 2022 | `MEASURED`, route `both`, verified multi-row crosstab |

- **Live questions.** Submitting a question runs keyword matching in `pickScenario()`
  (`gun|firearm` → ABSENT, `lonel|isolat` → RELATED, `hisp` → failed verification,
  `mean|average|rate|percent|crosstab|bmi` → verified stat, else the food-security answer) and then
  streams that scenario.
- **Rate limit.** Simulated 50 requests/hour; the indicator turns amber at 80 % and the input is
  replaced by a lock message at the cap. "Trigger rate limit" in the States menu jumps straight
  there.
- **Empty state.** Four clickable example questions that submit on click.
- **The hard rule holds.** `result-card` only renders when `sql.verified === true`, and the `c3`
  answer text contains no number.

### What does NOT exist

- No `services/api/` — no FastAPI app, no orchestrator, no RAG modules, no LLM gateway.
- No `jobs/ingest/` — no codebook parser, no CSV loader, no abort guard, no crosswalk.
- No `db/migrations/` — the schema exists only as SQL blocks inside `design.md`.
- No `eval/` harness.
- No `docker-compose.yml`, no `railway.json`, no Vercel config, no CI workflows.
- No tests of any kind.
- No `.env.example`.
- No Next.js app (see §12 — this matters).

---

## 5. Settled architecture

### Deployment topology

```
Browser
  └─ Vercel ─ Next.js App Router
        └─ server-side route handler proxy (/api/*)   ← the ONLY thing that knows
              │  HTTPS + API_SHARED_SECRET               the backend URL and keys
              ▼
       Railway ─ single persistent FastAPI container
              ├─ Embedding_Model resident in RAM (~420 MB, all-mpnet-base-v2)
              ├─ Orchestrator (fixed pipeline)
              ├─ pool A: read/write role  → query_log, conversations, messages, codebook reads
              └─ pool B: differ_ro role    → executes LLM-generated SQL ONLY
              ▼
       Supabase (paid tier) ─ Postgres 16 + pgvector
              codebook layer · survey layer · telemetry layer

       Groq  ← reached only through llm/gateway.py

       OFFLINE, never in the request path:
       Google Drive (codebook PDFs + survey CSVs) → Ingestion_Job → Postgres
```

### Stack and the reason for each choice

| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| Frontend | Next.js 15 App Router on **Vercel** | Route handlers proxy to the backend so `GROQ_API_KEY`, `DATABASE_URL`, and the Railway base URL never reach browser code |
| Backend | **FastAPI**, Python 3.11, Docker, on **Railway** | **Must be a persistent container, not serverless.** See the hard constraint below. |
| Database | **Postgres 16 + pgvector** on Supabase, paid tier | One store for vectors + survey data + telemetry means retrieval can join against data in a single query. No separate vector DB service. |
| Embeddings | sentence-transformers `all-mpnet-base-v2`, 768-dim, local | Must be the *same model at the same version* for ingestion and query time |
| LLM | **Groq** behind `llm/gateway.py` | Fast and cheap; the gateway is the only module importing the Groq SDK, so the provider is swappable in one line |
| Stats | **pandas + statsmodels** | Weighted means, design variables, clustered SEs later. No serious JS equivalent — this is why the backend is Python. |
| Auth | **none in v1** | Deferred deliberately. `conversations.user_id` is nullable with no FK so auth is additive later. |
| Queue | **none** | Ingestion is offline and there are no user uploads, so there is nothing to queue. No Celery, no Redis. |

### The hard constraint that shapes everything

The embedding model is **~420 MB and must be loaded in memory at query time**, because the user's
question has to be embedded with the same model that built the index.

- Rules out Vercel functions and AWS Lambda entirely.
- Requires a persistent container with **≥1 GB RAM**, always on (~$7/mo).
- Free tiers that spin down give 30–60 s cold starts on the first question.
- The alternative (a hosted embedding API) means re-embedding the whole corpus and paying per
  query. Not worth it at this scale.

### Decisions that are settled — do not relitigate in PRs

**No agent framework. No CrewAI, no LangChain, no LangGraph.** What this system is, is a
*pipeline with LLM decision points* (rewrite, route, judge, generate, verify, answer), not an
agent system. Agents choose their own control flow; this has fixed flow with explicit branches,
and the question shapes are enumerable.

The real reason is the **eval harness**: if the same question takes different paths on different
runs, the baseline stops being comparable and we lose the ability to tell whether a change helped.
That measurement discipline is the most valuable thing the project has.

LangChain specifically abstracts chunking and prompt construction — exactly where this project's
value lives. Retrieval here is a pgvector query of about fifteen lines; wrapping it makes
retrieval failures harder to debug.

If the fixed pipeline is ever genuinely outgrown, the right tool is **LangGraph** (an explicit,
inspectable state machine), not CrewAI — and even then, write it as plain Python first.

A **CI dependency-manifest check asserts `crewai`, `langchain`, and `langgraph` are absent.**

**Postgres vs Supabase is not a choice.** Postgres 16 + pgvector is the database. Supabase is a
host for it. Develop locally on `pgvector/pgvector:pg16` via docker-compose; deploy to Supabase.
Moving hosts is a connection-string change.

### Middleware order (`main.py`)

```python
app.add_middleware(CORSMiddleware, allow_origins=[settings.FRONTEND_URL])
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RequestIDMiddleware)    # correlation id -> query_log
app.add_middleware(TimingMiddleware)       # latency_ms -> query_log
app.add_middleware(RateLimitMiddleware)    # per-user; protects the Groq quota
```

Two of these are load-bearing, not nice-to-have. **Timing** populates `query_log.latency_ms`,
which is the only instrument for the concurrency risk in §14. **Rate limiting** sits *ahead of*
the orchestrator so a rejected request costs zero LLM spend.

### Environment variables

| Variable | Where | Notes |
|---|---|---|
| `DATABASE_URL` | API, ingest job | Read/write role |
| `DATABASE_URL_RO` | API | **Separate `differ_ro` role** — generated SQL only |
| `GROQ_API_KEY` | API only | Never in the frontend, never `NEXT_PUBLIC_` |
| `API_BASE_URL` | Vercel | Server-side only |
| `API_SHARED_SECRET` | Both | The proxy authenticates to the API with it |
| embedding model name + version | API, ingest job | A startup check asserts these match |

---

## 6. The fixed pipeline

The orchestrator is a plain Python async function. Branching is a fixed `if/else` on `intent` and
`verdict` — never a model-chosen next action. The function body *is* the specification.

```python
# services/api/orchestrator.py
async def handle_question(question, conversation_id, year) -> AsyncIterator[Event]:
    standalone = await rewrite.to_standalone(question, history, gateway=llm_gateway)
    intent     = await router.classify(standalone, gateway=llm_gateway)   # codebook | data | both
    yield Event.route(intent)

    candidates      = await retrieval.search(standalone, year=year)
    verdict, closest = await judge.classify_presence(standalone, candidates, gateway=llm_gateway)
    yield Event.verdict(verdict)

    if verdict == "ABSENT":
        yield Event.answer(not_measured_message(standalone))

    elif verdict == "RELATED":
        yield Event.citation(closest)                    # exactly the top candidate
        yield Event.answer(related_explanation(standalone, closest))

    else:  # MEASURED
        sql_result = None
        if intent in ("data", "both"):
            sql      = await sql_gen.generate(standalone, year, candidates, gateway=llm_gateway)
            problems = verify.check(sql, year)           # pure function
            yield Event.sql(sql, verified=not problems, problems=problems)
            if problems:
                yield Event.answer(verification_warning(problems))   # NO NUMBER
            else:
                sql_result = await readonly_store.execute(sql)       # differ_ro pool
                yield Event.data(sql_result.columns, sql_result.rows)
        if intent in ("codebook", "both") or sql_result is not None:
            yield Event.answer(await generate.compose(standalone, candidates, sql_result,
                                                      gateway=llm_gateway))

    await relational_store.write_query_log(question=standalone, verdict=verdict, ...)
```

The SSE event stream the frontend consumes:

```
route → verdict → citation(s) → sql → data → token* → done
```

The UI template's `StreamEvent` union already matches this shape, so swapping `mockAsk()` for a
real SSE reader should not require UI component changes.

### Module layout (to be created)

```
services/api/
├── main.py                 FastAPI app, middleware registration
├── config.py               pydantic Settings (env-driven)
├── orchestrator.py         the fixed pipeline above
├── api/routes/
│   ├── chat.py             POST /api/chat → SSE
│   ├── variables.py        GET /api/variables/search, /api/variables/{year}/{name}
│   ├── years.py            GET /api/years  (backed by variable_availability)
│   └── health.py
├── rag/
│   ├── rewrite.py          follow-up → standalone question
│   ├── router.py           intent: codebook | data | both
│   ├── retrieval.py        pgvector search + year filter + dedupe
│   ├── judge.py            MEASURED | RELATED | ABSENT via construct equivalence
│   ├── sql_gen.py          SQL with real value-label codes injected
│   ├── verify.py           pure function: codes vs stored value labels
│   └── generate.py         grounded answer composition + citation filter
├── analysis/
│   └── descriptive.py      weighted mean / crosstab using WTFA_A, PSTRAT, PPSU
├── llm/
│   └── gateway.py          LLMGateway protocol + GroqGateway (only Groq importer)
├── store/
│   ├── vector_store.py     vector search over codebook_variables / sections
│   ├── relational_store.py read/write pool — telemetry, conversations
│   └── readonly_store.py   differ_ro pool — generated SQL execution ONLY
└── middleware/
    ├── timing.py
    └── rate_limit.py

jobs/ingest/
├── run_ingest.py
├── abort_guard.py                  shared by both tracks, format-agnostic
├── parser_2019plus/
│   ├── codebook_parser.py
│   └── csv_loader.py
└── parser_legacy_2017_2018/        isolated — no shared parsing logic
    ├── codebook_parser.py
    ├── csv_loader.py
    └── crosswalk.py
```

### Key interfaces

```python
# llm/gateway.py — every stage depends on this protocol, injected at startup
class LLMGateway(Protocol):
    async def complete(self, *, system: str, messages: list[Message],
                       temperature: float = 0.0,
                       response_format: Literal["text","json"] = "text") -> LLMResponse: ...
    async def stream(self, *, system: str, messages: list[Message],
                     temperature: float = 0.0) -> AsyncIterator[str]: ...

# rag/judge.py
async def classify_presence(question, candidates, gateway) -> tuple[Verdict, Candidate | None]

# rag/sql_gen.py — codes injected into context, not left to model recall
async def generate(question, year, candidates, gateway) -> str

# rag/verify.py — PURE. Same function backs production and the property test.
def check(sql: str, year: int) -> list[Problem]        # empty list == verified

# analysis/descriptive.py
def weighted_mean(values, weights) -> float

# middleware/rate_limit.py — sliding window, independent state per user_id
class RateLimiter:
    def check(self, user_id: str, now: float) -> RateLimitResult
```

### API contract

```
POST /api/chat                            { question, conversation_id?, year? } → SSE
GET  /api/variables/search?q=&year=&limit=
GET  /api/variables/{year}/{name}         → record + value labels
GET  /api/years                           → years available, from variable_availability
GET  /api/health
```

---

## 7. Data model

Three layers in one Postgres instance. Full SQL lives in `design.md`; this is the shape.

### Codebook layer

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE sections (
    id bigserial PRIMARY KEY,
    survey_year int NOT NULL,
    code text NOT NULL,                 -- 'FDS'
    name text NOT NULL,                 -- 'Food Security'
    embedding vector(768),
    UNIQUE (survey_year, code)
);

CREATE TABLE codebook_variables (
    id bigserial PRIMARY KEY,
    survey_year int NOT NULL,
    variable_name text NOT NULL,
    section_code text,
    description text,
    question_text text,
    keywords text,
    universe text,                      -- raw skip-pattern expression, for grounding only
    page int NOT NULL,                  -- REQUIRED — citations depend on it
    is_recode boolean DEFAULT false,
    source_era text NOT NULL CHECK (source_era IN ('2019plus','legacy_2017_2018')),
    embed_text text,                    -- exactly what was embedded
    embedding vector(768),
    tsv tsvector GENERATED ALWAYS AS (...) STORED,
    UNIQUE (survey_year, variable_name)
);

CREATE TABLE value_labels (
    id bigserial PRIMARY KEY,
    variable_id bigint REFERENCES codebook_variables(id) ON DELETE CASCADE,
    code int NOT NULL,
    label text NOT NULL,
    is_sentinel boolean NOT NULL,       -- per (variable_id, code). NEVER a global rule.
    UNIQUE (variable_id, code)
);

CREATE INDEX ON codebook_variables USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON codebook_variables USING gin (tsv);
CREATE INDEX ON codebook_variables (survey_year, variable_name);
CREATE INDEX ON sections USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON value_labels (variable_id);
```

`value_labels.variable_id` being the scoping FK on every row is what *structurally* prevents
cross-variable sentinel leakage. `universe` is kept as free text surfaced in citations so a
researcher can see skip-pattern context — the 1,428-edge dependency graph and recursive-CTE
lineage traversal from `DIFFER_Architecture_v2.md` §5.2 are **not built** in this version.

### 2017–2018 crosswalk

```sql
CREATE TABLE legacy_crosswalk (
    id bigserial PRIMARY KEY,
    legacy_variable_name text NOT NULL,
    legacy_survey_year int NOT NULL CHECK (legacy_survey_year IN (2017, 2018)),
    mapped_variable_name text,          -- NULL when status = 'no_equivalent'
    mapped_survey_year int CHECK (mapped_survey_year >= 2019),
    status text NOT NULL CHECK (status IN ('mapped','no_equivalent')),
    notes text,
    UNIQUE (legacy_variable_name, legacy_survey_year)
);
```

Linked to `codebook_variables` **by name only, no foreign key** — deliberately. The dependency
points one direction (legacy → 2019+) and never the reverse, so the 2019+ parser cannot end up
depending on anything crosswalk-shaped.

### Survey data layer

```sql
-- One table per survey year, 2017–2023. FULL column coverage (~640 columns/year),
-- generated by the ingestion job from the parsed codebook — not hand-written.
CREATE TABLE survey_2023 (
    respondent_id bigserial PRIMARY KEY,
    survey_year int NOT NULL DEFAULT 2023,
    wtfa_a real,        -- final annual weight
    pstrat int,         -- pseudo-stratum
    ppsu  int,          -- pseudo-PSU
    ...                 -- one column per variable in that year's codebook
);

CREATE TABLE variable_availability (
    variable_name text PRIMARY KEY,
    years int[] NOT NULL      -- computed at ingestion, never inferred from search results
);
```

**Sizing.** Both architecture drafts sized around a ~120-variable subset to fit a ~500 MB free
tier. The requirements override that: full coverage is roughly 7 years × ~30 k rows × ~640
columns, landing in the **low single-digit GB** before indexes. That needs a Supabase paid tier
with headroom, re-checked before each year's ingestion run. Codebook vectors are negligible
(~3,500 variables × 768 dims × 4 bytes ≈ 11 MB).

### Telemetry layer

```sql
CREATE TABLE conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid,              -- NULLABLE, no FK yet. Written NULL in v1.
                               -- Reserved so auth is an added FK, not a breaking migration.
    default_year int,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE messages (
    id bigserial PRIMARY KEY,
    conversation_id uuid REFERENCES conversations(id) ON DELETE CASCADE,
    role text CHECK (role IN ('user','assistant')),
    content text,
    citations jsonb,           -- [{variable, section, page, year}]
    generated_sql text,
    sql_verified boolean,
    created_at timestamptz DEFAULT now()
);

-- One row per assistant turn. The eval harness and any future dashboard both read this.
CREATE TABLE query_log (
    id bigserial PRIMARY KEY,
    message_id bigint REFERENCES messages(id),
    conversation_id uuid,
    question text,
    rewritten_question text,
    intent text,               -- codebook | data | both
    verdict text,              -- MEASURED | RELATED | ABSENT
    top_variable text,
    top_similarity real,
    generated_sql text,
    sql_verified boolean,
    verification_notes jsonb,  -- eval runs record `strategy` here
    latency_ms int,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX ON query_log (created_at);
CREATE INDEX ON query_log (verdict);
```

`query_log` is designed now so the analytics dashboard is a read layer later, not a migration.
The single most interesting question it will answer: **which concepts do researchers repeatedly
ask about that return `ABSENT`?** That is a list of things NHIS does not measure but health
researchers want — a publishable finding that falls out of the telemetry for free.

### Read-only role

```sql
CREATE ROLE differ_ro LOGIN PASSWORD :'differ_ro_password';
GRANT CONNECT ON DATABASE differ TO differ_ro;
GRANT USAGE  ON SCHEMA public TO differ_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO differ_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO differ_ro;
-- No INSERT/UPDATE/DELETE/TRUNCATE/schema-modify grants of any kind.
```

---

## 8. The four safety-critical mechanisms

`design.md` names exactly four paths as defense-in-depth that must never silently fail. Any PR
touching these needs extra scrutiny, and each has a dedicated property test.

| # | Mechanism | Requirement | Property test | Failure mode it prevents |
|---|---|---|---|---|
| 1 | **Per-variable sentinel computation** at ingestion | Req 5 | P7 + a hard-coded `HISPALLP_A` fixture | A global "7/8/9 are missing" rule silently deletes a real racial category from every analysis |
| 2 | **SQL verification against stored value labels** | Req 6 | P6 + hand-written unit tests for `IN`/`NOT IN`/`=`/`BETWEEN` | The model invents codes (`NOT IN (77,88,99)`) and returns a plausible wrong number |
| 3 | **`differ_ro` read-only role** | Req 7 | Integration tests: `INSERT`/`UPDATE`/`DELETE`/`DROP` all rejected by Postgres | A flawed or malicious query modifies data when app-level checks fail |
| 4 | **Ingestion abort guard** | Req 9 | P9 | A year parses to zero variables and the system serves confident answers from an empty index |

Plus one non-negotiable product invariant, enforced structurally in the orchestrator **and**
already in the UI:

> **P5 — Verification gates numeric output.** A numeric result must never appear in a response
> unless a verification call for that exact SQL returned an empty problem list immediately before
> execution.

### Error-handling contract

| Path | Behavior |
|---|---|
| `ABSENT` verdict | State the concept is not measured. **Never** substitute an unrelated variable as if it answered the question. Log `verdict=ABSENT`. |
| `RELATED` verdict | Present exactly the single closest candidate **plus an explanation of how it differs**. No numeric result is attempted. |
| SQL verification fails | Warning naming the problem (e.g. "code 77 is not a defined value for `HISPALLP_A` in 2023"). SQL is still shown, tagged `verified: false`. **No number.** |
| Ingestion abort | That year aborts entirely — zero rows, no partial state. Report year + count. Other years in the run are unaffected. |
| `differ_ro` rejection | Converted into the same "no numeric result" response. Should be unreachable if `verify.check()` is correct — but it is not relied on as the only gate. |
| Rate limit exceeded | Rejected by middleware before the orchestrator. Zero LLM cost. Independent per user. |
| Crosswalk `no_equivalent` | **Not an error** — a defined outcome. Retrieval and citation code must handle it without raising. |
| LLM Gateway failure | Generic SSE failure event; logged in `query_log` with an error marker, **not** as if it succeeded. |

---

## 9. Ingestion design

Offline only. The `Ingestion_Job` is the **only** component with network access to Google Drive.
The orchestrator never imports a parser module and never reaches Drive or parses a PDF at query
time.

```
Google Drive
  ├─ 2019+ PDFs  → parser_2019plus/codebook_parser.py → one record per variable (full field set)
  │                                                     + compute_is_sentinel per (var, code)
  ├─ 2019+ CSVs  → parser_2019plus/csv_loader.py ───────────────────────────────→ survey_YYYY
  ├─ 2017–18 PDFs→ parser_legacy_2017_2018/codebook_parser.py → parsed legacy records
  │                parser_legacy_2017_2018/crosswalk.py ───────────────────────→ legacy_crosswalk
  │                                                       (read-only lookup against
  │                                                        codebook_variables, one direction)
  └─ 2017–18 CSVs→ parser_legacy_2017_2018/csv_loader.py ──────────────────────→ survey_YYYY
                                   │
                                   ▼
                     abort_guard.check_abort(survey_year, parsed_count, threshold)
                       parsed_count < threshold → ABORT year, 0 rows, report year + count
                       parsed_count ≥ threshold → embed → load
                                   │
                                   ▼
                     embed_text builder → all-mpnet-base-v2 (768-dim)
                                   │
                                   ▼
                     codebook_variables + value_labels
```

**Where the two tracks converge, and where they deliberately do not:**

- They converge **only at the data model** — both write `codebook_variables` / `value_labels` /
  `survey_YYYY`, tagged with `source_era`.
- They do **not** converge at the code level. The only shared module is `abort_guard.py`, which
  takes a plain `(survey_year, parsed_count)` pair and knows nothing about either file format.
  `parser_2019plus` never imports from `parser_legacy_2017_2018`. **A CI import-graph check
  enforces this.**
- The reason: the 2019 NHIS redesign changed both codebook format and survey layout. Keeping the
  legacy parser isolated means its quirks cannot corrupt or complicate the 2019+ path.

**Abort threshold:** defined per survey year and configurable, defaulting to a *fraction of the
prior year's known variable count* — so a format change that silently drops most fields is caught
even when the absolute count is nonzero.

---

## 10. Testing strategy

Two complementary layers. Both are necessary.

**Property-based tests** cover the pure logic — cheap, run on every commit, catch logic
regressions. 14 properties are specified in `design.md`, each implemented as one PBT with a
**minimum of 100 iterations** using Hypothesis, tagged:

```
Feature: differ-platform, Property N: {property text}
```

| # | Property | Validates |
|---|---|---|
| 1 | Grounding filter — cited variables are always a subset of retrieved ones | 1.3, 2.2 |
| 2 | Citation completeness — variable + section + page + year, all four, matching source | 2.1 |
| 3 | Verdict-conditioned response shape — ABSENT carries no cited answer; RELATED carries exactly the top candidate + explanation | 3.2, 3.3 |
| 4 | Weighted statistic correctness vs an independent reference (`numpy.average`) | 4.2 |
| 5 | **Verification gates numeric output** | 4.3, 6.3 |
| 6 | **SQL verifier correctness** — accepts iff every referenced code is a real value label | 6.1, 6.2 |
| 7 | **Sentinel computation independence** across variables sharing a code value | 5.1–5.3 |
| 8 | Crosswalk totality — always `mapped` or `no_equivalent`, never raises or returns null | 12.3 |
| 9 | **Ingestion abort guard** | 9.1–9.3 |
| 10 | Pipeline stage ordering — fixed per (intent, verdict); generate+verify appear iff a number is needed | 15.1 |
| 11 | LLM Gateway interface conformance across two fake implementations | 14.3 |
| 12 | Query log round trip | 17.1, 3.4 |
| 13 | Latency computation | 17.2 |
| 14 | Rate limiter correctness, including per-user independence under interleaving | 18.1–18.3 |

**The eval harness** covers the LLM judgment calls that have no deterministic ground truth
(rewriting, routing, presence judging, SQL quality). A fixed set of researcher-language questions
with a documented split — N `MEASURED` with known correct variable + year, M `ABSENT` known not to
be in NHIS, K `RELATED`-but-not-measured — run against the **real** pipeline.

- CI runs it on every PR touching `rag/`, `analysis/`, or `jobs/ingest/crosswalk.py` and **fails
  the PR if the pass rate regresses against the recorded baseline.**
- Each eval run writes to `query_log` with its `strategy` noted in `verification_notes`, so a
  prompt change's effect shows up in the same table production traffic uses. No second telemetry
  path to keep in sync.
- Prototype baseline to beat: **13/13 retrieval, 4/4 absence detection.**

**Targeted unit and integration tests** that property tests cannot replace:

- The `HISPALLP_A` sentinel fixture as a literal hard-coded test — guards against an
  implementation that passes the general property while still special-casing "7/8/9 is always
  sentinel".
- Hand-written SQL fragments (`IN`, `NOT IN`, `=`, `BETWEEN`) to pin exact `Problem` structure and
  messages.
- `differ_ro` write rejections against a **real** Postgres instance (infrastructure behavior, not
  logic).
- Each parser track against a small fixture PDF+CSV pair, asserting variable count, value labels,
  and survey rows land correctly.

**Static / manifest checks in CI:**

- Dependency manifest contains none of `crewai` / `langchain` / `langgraph`.
- `parser_2019plus` has no import from `parser_legacy_2017_2018`.
- No module outside `llm/gateway.py` imports the Groq SDK.
- The Vercel build output contains no reference to the Railway base URL, `GROQ_API_KEY`, or
  `DATABASE_URL`.
- `conversations.user_id` exists, is nullable, and is UUID-FK-compatible.
- Embedding model name + version match between the ingestion job and the orchestrator at startup.

---

## 11. What is explicitly out of scope

Naming these prevents scope drift. **Do not build them, and do not add schema for them.**

- **Cross-year DiD estimation** — treatment/control comparison across periods, feasibility gating,
  clustered standard errors.
- **Concept harmonization** — the `concepts`, `concept_mappings`, and `comparability` tables and
  the comparability gate on cross-year questions. (Designed in `DIFFER_Architecture_v2.md` §5.4;
  explicitly excluded here, *including the schema*.)
- **Analytics dashboard** — telemetry is collected so it can be built later; no views, no routes,
  no UI.
- **User authentication of any kind** — no magic links, no login, no domain allowlist. The user
  base spans gmail / .edu / other domains, so a **domain allowlist is invalid for this product and
  must not be built even as a stopgap.**
- **Multi-tenant workspaces.**
- **Chart generation.**
- **Restricted-use / NCHS Research Data Center (RDC) data**, including any state-level treatment
  variables not in the public-use file.
- **Horizontal scaling / multiple backend replicas** — single container only; scaling is a future
  decision point.
- **The variable dependency graph** (`variable_edges`, 1,428 edges, recursive-CTE lineage) — it is
  DiD-adjacent tooling. `universe` is kept as free text only.
- **Reranking** (`bge-reranker-v2-m3`) — only if eval ever shows it beats the judge, and the
  container memory budget allows.

---

## 12. Known discrepancies and gaps — read before coding

These are real inconsistencies in the current state. Resolve them explicitly rather than
discovering them mid-task.

### 1. The UI template is Vite + react-router, but Requirement 16.1 says Next.js App Router ⚠️

This is the biggest one. `requirements.md` Req 16.1 mandates a **Next.js (App Router) app on
Vercel**, specifically because Req 16.2/16.3 need **server-side route handlers** to proxy backend
calls so `GROQ_API_KEY`, `DATABASE_URL`, and the Railway base URL never reach browser code.

What was built is a **Vite SPA using `react-router-dom` v7** — which has no server side at all,
and therefore cannot satisfy the proxy requirement as written.

Three options:

- **(a) Port the template to Next.js.** The components are framework-agnostic (plain React +
  Tailwind + Radix); the work is `App.tsx` routes → `app/` directory, `<link>` font tags → `next/font`
  or the root layout, `react-router` `Link` → `next/link`, and adding
  `app/api/[...proxy]/route.ts`. Probably a day's work. **This is the path the spec assumes** —
  task 16.1 is literally "Scaffold the Next.js app in `apps/web/`".
- **(b) Keep Vite and add a separate server-side proxy** (a small Node/Hono service, or Vercel
  edge functions alongside the SPA). Satisfies the *intent* of Req 16.2/16.3, violates the letter
  of 16.1.
- **(c) Amend Req 16.1.** Legitimate, but it must be a deliberate, written decision, not drift.

Until this is settled, the frontend track cannot start task 16.

### 2. Repo layout does not match the spec's module layout

The spec expects `apps/web/`, `services/api/`, `jobs/ingest/`, `db/migrations/`, `eval/` at the
repo root (task 1.1). The repo currently has only `ui-template/` at the root. Task 1.1 should
include moving/renaming `ui-template/` → `apps/web/`.

### 3. The spec itself is not in the repo

`.kiro/specs/differ-platform/` and the architecture/UI markdown docs live in the **workspace
root**, outside the git repo. That means a new contributor cloning `DIFFERENT` gets the UI
template and nothing else. **These docs should be committed** (e.g. into `docs/` plus
`.kiro/specs/`) so the spec travels with the code.

### 4. `ui-template/README.md` is still the default Vite boilerplate

It says nothing about DIFFER. Replace it.

### 5. No `.env.example`, no secrets documentation in the repo

The variable table is in §5 of this file and in `DIFFER_Platform_Architecture.md` §10. It should
become a committed `.env.example`.

### 6. The UI template has no tests

Acceptable for a template, but the acceptance checklist in `01-ui-requirements.md` §9 was verified
by hand. If the UI is ported to Next.js, that checklist is the regression suite — consider
automating it.

### 7. Two UI-level values are currently hard-coded and will need to come from the API

- Rate limit `50/hour` is a constant in `rate-limit-indicator.tsx`. The real limit comes from
  `config.py` and should be surfaced by the API.
- `pickScenario()` keyword matching in `mock-stream.ts` is the fake router. It disappears entirely
  when the real SSE endpoint lands — the file is the designated swap point.

### 8. The `03-mock-data.md` field names are the contract

Snake_case throughout (`variable_name`, `survey_year`, `weight_variable`). The backend's SSE
payloads must match, or the UI breaks silently. Keep `types.ts` and the Python `Event` shapes in
sync deliberately.

---

## 13. Next steps — the build order

`tasks.md` has 21 top-level tasks, ~80 subtasks, each traced to a requirement, with a
dependency-wave graph. **All are unchecked.** Tasks marked `*` are test tasks and can be skipped
for a faster MVP; core implementation tasks are never optional.

In Kiro, open `.kiro/specs/differ-platform/tasks.md` and click **Start task** next to a task to
execute it. Start with 1.1.

### The critical path

```
1. Project scaffolding            monorepo layout, FastAPI skeleton, pydantic Settings
2. Schema + migrations            codebook / survey / telemetry tables, differ_ro role  ⚠️
3. CHECKPOINT
4. Ingestion: 2019+ track         abort guard ⚠️, codebook parser + sentinels ⚠️,
                                  CSV loader, embeddings, run_ingest
5. Ingestion: legacy 2017–18      isolated parser, CSV loader, crosswalk
6. CHECKPOINT
7. LLM Gateway                    protocol, GroqGateway, FakeLLMGateway
8. RAG stages                     rewrite, router, retrieval, vector_store, judge, generate
9. CHECKPOINT
10. SQL gen + verify + analysis   sql_gen, verify.check ⚠️, descriptive stats, readonly_store ⚠️
11. Orchestrator                  the fixed pipeline, SSE events
12. Telemetry                     timing middleware, query_log writes
13. Rate limiting                 sliding window, registered ahead of the orchestrator
14. CHECKPOINT
15. API routes                    chat (SSE), variables, years, health
16. Frontend                      Next.js app, server-side proxy ⚠️, chat UI, citations, SQL panel
17. CHECKPOINT
18. Eval harness                  fixed question set, runner, CI gate
19. Static/config checks          embedding model version consistency
20. Deployment                    docker-compose, Railway, Vercel, Supabase provisioning
21. FINAL CHECKPOINT
```

### Dependency waves (from `tasks.md`) — what can run in parallel

```
wave 0  1.1
wave 1  1.2  1.3  2.1  2.2  2.3  2.4  16.1
wave 2  1.4  2.5  2.7  4.1  4.3  4.6  5.1  5.2  7.1  8.4  10.2  10.5  11.1  12.1  13.1  20.1
wave 3  2.6  4.2  4.4  4.5  4.7  5.3  5.7  7.2  7.3  8.1  8.2  8.3  8.5  10.3  10.4  10.6
        10.7  12.2  13.2  13.3  20.4
wave 4  4.8  5.4  7.4  7.5  8.6  8.7  10.1
wave 5  4.9  5.5  8.8  8.9  10.8  11.2
wave 6  5.6  11.3  11.4  12.3  15.1  15.2  15.3  15.4  18.1
wave 7  12.4  15.5  16.2  18.2  19.1
wave 8  16.3  16.4  16.5  18.3  20.2
wave 9  16.6  20.3
```

`run_ingest.py`, `orchestrator.py`, and `main.py` are each edited by more than one task — the waves
are arranged so those edits never land concurrently.

### Suggested split for three contributors

The 6-track model in `DIFFER_Architecture_v2.md` is obsolete at this team size. A workable split:

| Person | Owns | First deliverable |
|---|---|---|
| **A — Data** | `db/migrations/`, `jobs/ingest/` (both tracks), `store/relational_store.py` | One survey year fully in Postgres, with correct per-variable sentinels and the abort guard passing its property test |
| **B — Retrieval & generation** | `llm/gateway.py`, `rag/*`, `analysis/descriptive.py`, `store/vector_store.py`, `store/readonly_store.py` | Vector search returning correct variables; judge hitting 4/4 on absence; verified SQL end to end |
| **C — API & frontend** | `orchestrator.py`, `api/routes/`, `middleware/`, `apps/web/` | Chat UI streaming real SSE from the orchestrator, against stubs until B's modules land |

**C never blocks on A or B** — build against the interfaces in §6 with stubbed implementations from
day one. That is the whole point of stubbing the Python interfaces in week 1.

Whoever owns `eval/` decides whether retrieval changes ship. That is not a junior role.

### Immediate first actions, concretely

1. **Settle the Next.js vs Vite question** (§12.1). It gates all frontend work.
2. **Commit the spec and architecture docs into the repo** (§12.3).
3. **Run task 1.1** — restructure to `apps/web/` + `services/api/` + `jobs/ingest/` + `db/migrations/`
   + `eval/`, with root lint/format/test config.
4. **Stand up `docker-compose.yml`** with `pgvector/pgvector:pg16` so everyone develops against a
   real local Postgres from the start (task 20.1 is in wave 2 for exactly this reason).
5. **Write the migrations (task 2) and the `differ_ro` role** before any ingestion code. The schema
   is the thing six-to-three people all depend on; it needs agreement before code, and migration
   PRs should require two reviewers.
6. **Answer the open decisions in §14** — two of them block the first ingestion run.

---

## 14. Open decisions that block work

| # | Decision | Owner | Blocks | Notes |
|---|---|---|---|---|
| 1 | **Next.js port vs Vite + separate proxy vs amend Req 16.1** | Team | All of task 16 | See §12.1. Needs a written decision. |
| 2 | **Which Supabase paid tier, and is it provisioned?** | Ops | Task 2, first ingestion | Full scope is low-single-digit GB + HNSW overhead. Re-check headroom before each year's run. |
| 3 | **Are the Drive folders of codebook PDFs and survey CSVs complete for 2017–2023?** | Research lead | Task 4, 5 | The legacy track assumes 2017–2018 are in a different format. Confirm before writing the parser. |
| 4 | **Abort threshold per year** — what counts as "too few variables"? | Data owner | Task 4.1 | Default is a fraction of the prior year's count. Needs real per-year numbers. |
| 5 | **Conversation history: full, last-N, or summarized?** | API owner | Task 8.1 (`rewrite`) | Direct LLM cost implication. |
| 6 | **Rate limit values** (requests, window) | Ops | Task 13.1 | UI currently fakes 50/hour. |
| 7 | **Groq model choice** (Llama 3.3 70B was the draft assumption) | Retrieval owner | Tasks 7–8 | Affects latency and cost per question. |
| 8 | **Is RDC (restricted-use) access on the roadmap at all?** | Research lead | The v2 DiD roadmap | Determines whether **state-level** DiD is ever possible — see §15. |

### Tracked operational risk (Requirement 19)

The system is designed for **30–40 concurrent researchers on a single container** with a ~420 MB
model resident. That is an explicit assumption, **not** a tested result. It must be **monitored via
`query_log.latency_ms`** rather than assumed acceptable. Horizontal scaling is deliberately out of
scope — but if latency degrades under real load, this is the first thing to revisit.

---

## 15. Future roadmap (v2 and beyond)

The current schema is designed so these are **additive**, not migrations. That is the main reason
the data model is more elaborate than v1 strictly needs.

### v2.1 — Concept harmonization

Introduce `concepts` (a research construct, stable across years), `concept_mappings` (how each
year measures it, including `recode_sql`), and `comparability` (explicit `comparable` / `caution` /
`not_comparable` status per concept per year-pair).

`comparability` becomes the **gate on every cross-year answer**. NHIS was redesigned in 2019 and
2020 fieldwork was disrupted by COVID. If a concept is `not_comparable` across a requested window,
the system **refuses to compute a trend and explains why**. That refusal is a correct answer, not
a failure.

### v2.2 — Differences-in-Differences estimation

DiD compares an outcome across groups and periods. What NHIS public-use data can and cannot
support:

| DiD requirement | NHIS public-use file |
|---|---|
| Outcome | Any harmonized concept |
| Period | `survey_year` — repeated cross-sections, which DiD supports |
| Treatment group | **`REGION` (4 census regions) or `URBRRL` only** |
| Weights | `WTFA_A` |
| Variance | `PSTRAT`, `PPSU` |

**The binding constraint: there is no state identifier in the public-use file.** Most policy DiD
designs — Medicaid expansion, state minimum wage, ACA provisions — need state-level treatment
assignment, which requires NCHS Research Data Center access.

The system **must say this plainly** when someone asks a state-level question, not quietly compute
something at region level and let them believe they got states.

A four-step **feasibility gate** runs before any estimation: (1) is the outcome concept mapped in
all requested years? (2) is it `comparable` across the span? (3) is the grouping variable available?
(4) are cell sizes adequate after sentinel exclusion? Fail any → explain instead of estimate.

Every DiD result must carry: point estimate, SE, 95 % CI; unweighted n per group × period cell; a
**parallel-trends caveat** (with only two pre-periods it is untestable and the system should say
so); comparability status for every year in the window; and the exact panel query used.
**Never return a bare number for a causal estimate.**

Specs are stored in an `analysis_runs.spec` jsonb column so every estimate is reproducible and
auditable.

### v2.3 — Analytics dashboard

A read layer over `query_log`, `feedback`, and `analysis_runs`. No migration needed — the
telemetry is already being collected. The headline question: **which concepts repeatedly return
`ABSENT`?**

### Also deferred

- Pre-2017 survey years (would need a third parser and crosswalk).
- Reranking, if and only if eval shows it beats the presence judge within the container's memory
  budget.
- User feedback capture (`feedback` table with ±1 rating) — designed in the v2 draft, not in the
  current schema.
- Authentication, when it is actually needed. `conversations.user_id` is already waiting for it.

---

## 16. How to run what exists today

```bash
git clone https://github.com/Different-platform/DIFFERENT.git
cd DIFFERENT/ui-template
npm install
npm run dev          # Vite dev server — open the printed localhost URL
```

Other scripts:

```bash
npm run build        # tsc -b && vite build   (verified passing)
npm run lint         # oxlint
npm run preview      # serve the production build
```

**What to click to understand the product in five minutes:**

1. Land on `/`, read the three feature cards, hit **Start asking**.
2. In the sidebar, open **"Weighted mean BMI, 2023"** — the happy path: verified SQL, a result
   table, a weight-variable footer.
3. Open **"Hispanic origin breakdown"** — the whole point of the project. SQL marked **Not
   verified**, two problems listed, **no number anywhere**.
4. Open **"Firearm ownership"** — the `ABSENT` answer. No citations, no hedging.
5. Open **"Loneliness measures"** — the `RELATED` answer. Closest variable plus an explanation of
   how it differs.
6. Type *"what's the average BMI"* and watch the artifact order stream in.
7. **States → Trigger rate limit** to see the locked input.
8. Narrow the window below 1024 px to see the sidebar collapse into a slide-over.

There is nothing to run for the backend. It does not exist yet.

---

## 17. Working conventions

### Branching

```
main                    protected; deploys to prod
dev                     integration; all PRs target this
feat/<area>-<thing>     e.g. feat/ingest-sentinel-parse
fix/<area>-<thing>
```

### Branch protection on `dev`

- One approving review from a code owner.
- CI must pass.
- **The eval job must pass on any PR touching `rag/` or `analysis/`.** This is the important one:
  it means retrieval changes get *measured* against the eval set instead of argued about in review.
- **Migrations require two reviewers** — they are the one thing that breaks everyone at once.

### PR template

```markdown
## What
## Area
## Eval impact
- [ ] Doesn't touch retrieval/analysis
- [ ] Eval run: __/13 retrieval, __/4 absence   (baseline 13/13, 4/4)
## Safety-critical paths touched?  (sentinels / verify / differ_ro / abort guard)
## Screenshots (frontend only)
## Migration? If yes, tag a second reviewer
```

### Code conventions

- **Python 3.11**, FastAPI, pydantic settings. Pure functions wherever a property test needs to
  reach them — `verify.check()` is the model: the same function backs production and the test
  suite, so there is no second "test implementation" to drift.
- **TypeScript**, strict. Path alias `@/` → `src/`. Components presentational; all state owned by
  the page.
- **Snake_case in API payloads and types** (`variable_name`, `survey_year`, `weight_variable`) —
  the contract in `03-mock-data.md`.
- **Semantic Tailwind utilities only** (`bg-background`, `text-muted-foreground`). Never hardcoded
  colors — the token set in `02-ui-design.md` §3 is the source of truth.
- **IBM Plex Mono** for variable names, SQL, numbers, tables, and badges. **IBM Plex Sans** for
  everything else.
- Any pipeline stage touching an LLM goes through `LLMGateway`. Importing the Groq SDK anywhere
  else fails CI.

### Attribution

Some patterns were taken from the MIT-licensed `PDF-Assistant-RAG` project — the `rrf_merge()`
RRF implementation, the SSE streaming pattern, the `docker-compose.yml` structure, the deploy
config layout, the CI workflow layout, and the pydantic settings pattern. **Attribute these in a
`NOTICE` file and in file headers where copied.**

---

## 18. Glossary

| Term | Meaning |
|---|---|
| **NHIS** | National Health Interview Survey — the public-use dataset this platform answers questions about |
| **Codebook** | The reference PDF describing every variable: name, section, description, question text, universe, data type, value labels |
| **Variable** | A single named column in a survey year, e.g. `HISPALLP_A`. One codebook record per variable per year. |
| **Section** | A grouping of related variables within a year's codebook, e.g. "Food Security" |
| **Value Label** | The human-readable meaning of a numeric code, e.g. `1` = "Yes" |
| **Sentinel Code** | A numeric code meaning missing/refused/unknown/not-applicable **for a specific variable**. Never a global rule. |
| **Universe / Skip Pattern** | The condition under which a respondent was asked a variable, e.g. "asked only if `HHSTAT_A=1`". Determines whether missingness is *structural* rather than random. |
| **Presence_Judge** | The stage classifying retrieved candidates against the question as `MEASURED` / `RELATED` / `ABSENT` |
| **Presence Verdict** | `MEASURED` (directly captured), `RELATED` (something adjacent exists but doesn't answer it), `ABSENT` (NHIS doesn't measure this) |
| **SQL_Generator** | The stage producing SQL from the question + retrieved variable context, with real codes injected |
| **SQL_Verifier** | The stage checking generated SQL against actual sentinel codes and value labels **before** any number is returned |
| **Analysis_Engine** | `analysis/descriptive.py` — weighted means and crosstabs using `WTFA_A`, `PSTRAT`, `PPSU` |
| **Orchestrator** | The backend component sequencing the fixed pipeline for each question |
| **LLM_Gateway** | The thin provider wrapper (`llm/gateway.py`); the only Groq SDK importer |
| **Embedding_Model** | `all-mpnet-base-v2`, 768-dim, used at **both** ingestion and query time, same version |
| **Ingestion_Job** | The offline process parsing Drive PDFs/CSVs into Postgres. Never in the request path. |
| **Abort Guard** | The ingestion safeguard halting a year's load if parsed variable count falls below threshold |
| **Read-Only Role** | `differ_ro` — `SELECT`-only Postgres role used exclusively to execute generated SQL |
| **Query_Log** | The telemetry table, one row per assistant turn: question, verdict, SQL, verification outcome, latency |
| **Rate_Limiter** | Per-user sliding-window middleware bounding LLM spend |
| **Weighted Statistics** | Statistics computed with the survey's final annual weight and design variables, not naive aggregation |
| **Crosswalk** | The structural mapping from a 2017–2018 variable to its 2019+ equivalent, or a `no_equivalent` flag. Built during ingestion. Distinct from Harmonization. |
| **Harmonization** | Mapping a research *construct* to comparable variables across years, including comparability across the 2019 redesign. **v2.** |
| **DiD** | Differences-in-Differences — causal estimation comparing an outcome across treatment groups and time periods. **v2.** |
| **RDC** | NCHS Research Data Center — restricted-use access; the only route to state-level identifiers. Out of scope. |

---

## Reading order for a new contributor

1. This file, §1–§4 — what it is, why, and what exists.
2. `.kiro/specs/differ-platform/requirements.md` — the contract. 20 requirements, read all of them.
3. `.kiro/specs/differ-platform/design.md` — the how. Pay particular attention to the Correctness
   Properties section.
4. `.kiro/specs/differ-platform/tasks.md` — pick a task.
5. Run the UI template (§16) and click through the six demo states. Especially the
   failed-verification one — it is the clearest 30-second explanation of why this project exists.
6. `DIFFER_Platform_Architecture.md` §2b and §5 — for the reasoning behind the two safety gates,
   if you want the argument rather than the conclusion.

**If you are an AI assistant reading this:** `requirements.md` is authoritative over `design.md`,
which is authoritative over the two architecture drafts, which are historical. Nothing in §11
should be built. The four mechanisms in §8 must never be simplified away. The discrepancies in §12
are known and unresolved — flag them rather than silently picking a side.
