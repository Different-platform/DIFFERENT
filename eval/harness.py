"""Eval harness — the measurement discipline the fixed pipeline exists for.

Design (design.md — Eval harness): a fixed set of researcher-style questions
with known expected verdicts, run against the **real** pipeline (the real
Groq-backed gateway, not the fake), scoring each question's verdict and — for
data questions — its `sql_verified` outcome against a recorded baseline. A run
fails if the pass rate regresses below the baseline.

Why this and not more property tests: property tests cover pure logic cheaply
on every commit. They cannot tell you whether the *judge* got the right answer
on a real question, because that is an LLM reasoning call with no deterministic
ground truth. The eval harness is the only thing that measures judgement
quality, and the fixed pipeline (Req 15) exists precisely so these numbers stay
comparable across runs.

Each run writes one `query_log` row per question via the orchestrator's normal
path, so a prompt change's effect shows up in the same table production traffic
uses. (Tagging those rows with `strategy` in `verification_notes` — so eval
traffic is distinguishable from production in the table — needs a small
orchestrator change to thread an eval marker through; tracked as a follow-up.
For now eval rows are identified by their questions and run timing.)

Usage, from the repo root:
    .venv/bin/python -m eval.harness run                 # run, score, print
    .venv/bin/python -m eval.harness run --save-baseline # record a new baseline
    .venv/bin/python -m eval.harness run --check         # fail (exit 1) on regression
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.api.llm.gateway import build_gateway
from services.api.orchestrator import Orchestrator
from services.api.store import db

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS_PATH = EVAL_DIR / "questions.json"
BASELINE_PATH = EVAL_DIR / "baseline.json"

#: Recorded with each eval query_log row so a prompt/model change is traceable
#: in the same table production uses. Bump when the strategy meaningfully
#: changes (e.g. a new model or a reworked judge prompt).
STRATEGY = "eval-v1"

#: Seconds to wait between questions, to stay under the Groq free-tier
#: tokens-per-minute limit (8000 TPM on `openai/gpt-oss-120b`). Each question
#: spends ~1-3k tokens across its several calls; ~8s spacing keeps a full run
#: comfortably under the cap. Raise if the tier is lower, lower it (or remove
#: it) on a paid tier.
EVAL_REQUEST_SPACING_SECONDS = 8.0


@dataclass
class QuestionResult:
    id: str
    question: str
    expect_verdict: str
    actual_verdict: str | None
    expect_variable: str | None
    citations: list[str]
    expect_verified: bool | None
    actual_verified: bool | None
    had_result: bool
    error: str | None
    passed: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "expect_verdict": self.expect_verdict,
            "actual_verdict": self.actual_verdict,
            "expect_verified": self.expect_verified,
            "actual_verified": self.actual_verified,
            "had_result": self.had_result,
            "passed": self.passed,
            "reasons": self.reasons,
        }


def load_questions() -> tuple[int, list[dict]]:
    data = json.loads(QUESTIONS_PATH.read_text())
    return data.get("survey_year"), data["questions"]


async def _run_one(
    orchestrator: Orchestrator, spec: dict, survey_year: int | None
) -> QuestionResult:
    """Drive the real pipeline for one question and collect its events."""
    verdict: str | None = None
    citations: list[str] = []
    verified: bool | None = None
    had_result = False
    error: str | None = None

    async for event in orchestrator.handle_question(
        spec["question"], survey_year=survey_year
    ):
        kind = event.get("type")
        if kind == "verdict":
            verdict = event["verdict"]
        elif kind == "citations":
            citations = [c["variable_name"] for c in event["citations"]]
        elif kind == "sql":
            verified = event["sql"]["verified"]
        elif kind == "result":
            had_result = True
        elif kind == "error":
            error = event["message"]

    return _score(spec, verdict, citations, verified, had_result, error)


def _score(
    spec: dict,
    verdict: str | None,
    citations: list[str],
    verified: bool | None,
    had_result: bool,
    error: str | None,
) -> QuestionResult:
    """Decide pass/fail for one question and record why.

    Scoring rules, deliberately strict on the safety-critical axis and lenient
    where the design itself treats outcomes as judgement calls:

    - Verdict must match expectation, EXCEPT a RELATED expectation also accepts
      ABSENT (and vice versa): the MEASURED-vs-not boundary is what matters for
      safety; the RELATED/ABSENT distinction is a softer judgement the design
      does not treat as a hard failure.
    - For a data question, `expect_verified` must match `sql_verified`, and a
      verified question must actually have produced a result (a number).
    - `expect_variable`, when given, must appear among the citations for a
      MEASURED/RELATED question. Membership, not sole-answer.
    - Any pipeline error is an automatic fail.
    """
    reasons: list[str] = []
    expect_verdict = spec["expect_verdict"]

    if error is not None:
        return QuestionResult(
            id=spec["id"], question=spec["question"], expect_verdict=expect_verdict,
            actual_verdict=verdict, expect_variable=spec.get("expect_variable"),
            citations=citations, expect_verified=spec.get("expect_verified"),
            actual_verified=verified, had_result=had_result, error=error,
            passed=False, reasons=[f"pipeline error: {error}"],
        )

    # Verdict (with the RELATED<->ABSENT softness).
    soft_pair = {"RELATED", "ABSENT"}
    verdict_ok = verdict == expect_verdict or (
        expect_verdict in soft_pair and verdict in soft_pair
    )
    if not verdict_ok:
        reasons.append(f"verdict {verdict!r} != expected {expect_verdict!r}")

    # Expected variable(s) among citations (only meaningful when something is
    # cited). `expect_variable` requires that exact variable; `expect_variable_any`
    # accepts any one of a set, for concepts with several valid variables.
    if expect_verdict == "MEASURED" and spec.get("expect_intent") != "data":
        expect_any = spec.get("expect_variable_any")
        expect_one = spec.get("expect_variable")
        if expect_any is not None:
            if not (set(expect_any) & set(citations)):
                reasons.append(
                    f"none of {expect_any} in citations {citations}"
                )
        elif expect_one and expect_one not in citations:
            reasons.append(
                f"expected variable {expect_one!r} not in citations {citations}"
            )

    # Data-question verification outcome.
    expect_verified = spec.get("expect_verified")
    if expect_verified is not None:
        if verified != expect_verified:
            reasons.append(
                f"sql_verified {verified!r} != expected {expect_verified!r}"
            )
        if expect_verified and not had_result:
            reasons.append("expected a verified result but no number was produced")

    return QuestionResult(
        id=spec["id"], question=spec["question"], expect_verdict=expect_verdict,
        actual_verdict=verdict,
        expect_variable=spec.get("expect_variable") or (
            "/".join(spec["expect_variable_any"])
            if spec.get("expect_variable_any") else None
        ),
        citations=citations, expect_verified=expect_verified,
        actual_verified=verified, had_result=had_result, error=None,
        passed=not reasons, reasons=reasons,
    )


async def run_eval() -> list[QuestionResult]:
    """Run every question against the real pipeline, sequentially.

    Sequential by design: the point is a stable, comparable measurement, and
    parallel LLM calls add nondeterministic ordering and rate-limit noise for
    no benefit on a question set this size.
    """
    from jobs.ingest.embedder import Embedder

    survey_year, questions = load_questions()

    db.open_pool()
    embedder = Embedder()
    embedder.model  # materialize once up front
    gateway = build_gateway()
    orchestrator = Orchestrator(gateway=gateway, embedder=embedder)

    results: list[QuestionResult] = []
    for index, spec in enumerate(questions):
        if index > 0:
            # Pace requests to stay under Groq's free-tier tokens-per-minute
            # cap. Each question makes several LLM calls; without a gap a
            # sequential run trips the 429 TPM limit and fails questions for a
            # reason that has nothing to do with pipeline quality. A retry on
            # 429 would be more elegant but belongs in the gateway, not here.
            await asyncio.sleep(EVAL_REQUEST_SPACING_SECONDS)
        results.append(await _run_one(orchestrator, spec, survey_year))
    return results


def summarize(results: list[QuestionResult]) -> dict[str, Any]:
    passed = sum(1 for r in results if r.passed)
    return {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "strategy": STRATEGY,
        "results": [r.to_dict() for r in results],
    }


def print_report(summary: dict[str, Any]) -> None:
    print(f"\nEval run — strategy {summary['strategy']}")
    print(f"  {summary['passed']}/{summary['total']} passed "
          f"({summary['pass_rate'] * 100:.0f}%)\n")
    for r in summary["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        line = f"  [{mark}] {r['id']:3} verdict={r['actual_verdict']}"
        if r["expect_verified"] is not None:
            line += f" verified={r['actual_verified']}"
        print(line)
        for reason in r["reasons"]:
            print(f"         - {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run the eval set")
    run_p.add_argument("--save-baseline", action="store_true",
                       help="record this run's pass rate as the new baseline")
    run_p.add_argument("--check", action="store_true",
                       help="exit 1 if pass rate regresses below the baseline")
    args = parser.parse_args()

    try:
        results = asyncio.run(run_eval())
    finally:
        db.close_pool()

    summary = summarize(results)
    print_report(summary)

    if args.save_baseline:
        BASELINE_PATH.write_text(
            json.dumps(
                {"pass_rate": summary["pass_rate"], "strategy": STRATEGY,
                 "per_question": {r["id"]: r["passed"] for r in summary["results"]}},
                indent=2,
            ) + "\n"
        )
        print(f"\nBaseline saved: pass_rate={summary['pass_rate']}")
        return 0

    if args.check:
        if not BASELINE_PATH.exists():
            print("\nNo baseline recorded; run with --save-baseline first.",
                  file=sys.stderr)
            return 1
        baseline = json.loads(BASELINE_PATH.read_text())
        if summary["pass_rate"] < baseline["pass_rate"]:
            print(f"\nREGRESSION: pass_rate {summary['pass_rate']} < "
                  f"baseline {baseline['pass_rate']}", file=sys.stderr)
            return 1
        print(f"\nOK: pass_rate {summary['pass_rate']} >= "
              f"baseline {baseline['pass_rate']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
