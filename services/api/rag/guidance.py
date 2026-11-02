"""Advise on using a statistic that was already computed.

"How can I use this in my research?" asks what the figure is good for, what it
cannot support, and what to report. The useful answer depends on facts about
this particular computation: which weight it used, which year, whether the
measure was derived by formula, which missing-data codes were dropped, and
that no standard error was computed. Those facts are written here as fixed
research notes built from the saved SQL and the codebook, so the cautions are
true for the figure and do not depend on the model remembering them.

The model turns the notes into an answer to the researcher's actual question.
The same number check as rag/explain.py applies: every number in the reply
must appear in the material it was given. A reply that fails twice falls back
to the notes themselves, which always make a complete, correct answer.
"""

from __future__ import annotations

import re

from services.api.domain import Hit
from services.api.llm.gateway import LLMGateway, Message
from services.api.rag import derivations, explain

SYSTEM_PROMPT = """\
You advise a health researcher on how to use a statistic you already computed \
from U.S. National Health Interview Survey (NHIS) data.

You are given the researcher's message, the question the figure answered, the \
summary you already gave, the exact SQL that ran, codebook details for the \
variables it used, and research notes that are true for this figure.

Answer the researcher's message directly in two to four short paragraphs of \
plain prose. Cover what the figure is suitable for (for example describing the \
U.S. adult population, giving background for a study, or checking against \
published estimates), what it cannot support without further work (for \
example significance tests, margins of error, or causal claims), and what to \
report in a methods section. Base every caution on the research notes, and \
put the ones that matter most for the message first.

Rules:
- Use only facts in the material provided. Do not state any number that is \
not in it: no sample sizes, cut-offs, other statistics, or page numbers.
- Do not recompute the result or restate it with different precision.
- The statistic WAS computed. Never say it cannot be computed.
- State each caution exactly as strongly as the notes do; do not widen it.
- No headings, no bullet lists, no markdown. Separate paragraphs with a blank \
line.\
"""

_WEIGHT = re.compile(r"\b(WTFA_SA|WTFA_A)\b", re.IGNORECASE)


def _weight_of(sql: str) -> str:
    match = _WEIGHT.search(sql or "")
    return match.group(1).upper() if match else "WTFA_A"


def _bmi_was_derived(sql: str) -> bool:
    """True when the SQL computed BMI itself from height and weight.

    Read from the SQL, not the question: "share with BMI over 30" may be
    answered from the published BMICAT_A recode, and calling that a formula
    we applied would be false.
    """
    bmi = next(d for d in derivations.DERIVATIONS if d.name == "body mass index")
    return "703" in (sql or "") and set(bmi.inputs) <= set(explain.variable_names(sql))


def research_notes(*, sql: str, variables: list[Hit]) -> list[str]:
    """True statements about this figure that a researcher needs to know.

    Built only from the saved SQL and codebook details, so each note can be
    checked against what actually ran.
    """
    weight = _weight_of(sql)
    year = explain.year_of(sql)
    names = explain.variable_names(sql)
    notes: list[str] = []

    notes.append(
        f"The figure is a population-weighted point estimate using the NHIS "
        f"sample-adult weight {weight}. No standard error or confidence "
        f"interval was computed, because the survey's design variables "
        f"(PSTRAT for strata and PPSU for primary sampling units) were not "
        f"used. To report a margin of error, test a difference, or fit a "
        f"model, re-estimate it in survey software with strata PSTRAT, "
        f"clusters PPSU and weight {weight}, for example the R survey package, "
        f"Stata's svy commands, or SAS PROC SURVEYMEANS."
    )

    if year is not None:
        notes.append(
            f"It describes U.S. civilian noninstitutionalized adults in {year}, "
            f"at one point in time. A cross-sectional estimate like this can "
            f"describe levels and prevalence and give context for a study; it "
            f"cannot show cause and effect or change within the same people."
        )

    if _bmi_was_derived(sql):
        notes.append(
            "BMI here is not a variable NHIS publishes. It was derived from "
            "self-reported height and weight as 703 x weight in pounds / "
            "(height in inches) squared. People tend to under-report weight "
            "and over-report height, so BMI from self-report usually runs "
            "lower than BMI from measured height and weight; say so if you "
            "compare it with measured data such as NHANES."
        )

    missing: list[str] = []
    for hit in variables:
        for label in hit.sentinel_labels:
            text = (label.label or "").strip()
            if text and text not in missing:
                missing.append(text)
    if missing:
        notes.append(
            f"Respondents with a missing-data code on any variable used "
            f"({', '.join(missing)}) were left out, so the estimate covers "
            f"adults with valid answers. State this exclusion in your methods."
        )
    else:
        notes.append(
            "Respondents with missing-data codes on the variables used were "
            "left out, so the estimate covers adults with valid answers. State "
            "this exclusion in your methods."
        )

    source = (
        f"National Center for Health Statistics, National Health Interview "
        f"Survey, {year} Sample Adult public-use file"
        if year is not None
        else "National Center for Health Statistics, National Health Interview "
        "Survey, Sample Adult public-use file"
    )
    used = f" and name the variables used ({', '.join(names)})" if names else ""
    notes.append(
        f"Cite the data as {source}{used}. The exact SQL shown with the "
        f"original answer documents how the figure was produced."
    )

    notes.append(
        "Survey years 2019 to 2023 all use the questionnaire NHIS introduced "
        "in its 2019 redesign, so they can generally be compared once you "
        "have checked that each variable's wording and response options are "
        "the same in each year's codebook. Estimates from 2018 or earlier are "
        "not directly comparable because of that redesign. In 2020 interviews "
        "moved largely to telephone during the COVID-19 pandemic, so NCHS "
        "advises caution when comparing 2020 estimates with other years."
    )
    return notes


def fallback(notes: list[str]) -> str:
    """Used when no grounded reply can be produced: the notes themselves."""
    return "Here is how to use this figure in research.\n\n" + "\n\n".join(notes)


def build_prompt(
    *,
    message: str,
    prior_question: str,
    summary: str,
    sql: str,
    variables: list[Hit],
    notes: list[str],
    feedback: str | None = None,
) -> str:
    details = "\n".join(explain.describe_variable(v) for v in variables) or "(none)"
    prompt = (
        f"The researcher's message: {message}\n\n"
        f"The question the figure answered: {prior_question}\n\n"
        f"Summary you already gave:\n{summary}\n\n"
        f"The exact SQL that ran:\n{sql}\n\n"
        f"Codebook details for the variables it used:\n{details}\n\n"
        f"Research notes (true for this figure):\n"
        + "\n".join(f"- {note}" for note in notes)
        + "\n\nAnswer the researcher's message."
    )
    if feedback:
        prompt += f"\n\n{feedback}"
    return prompt


async def compose(
    *,
    message: str,
    prior_question: str,
    summary: str,
    sql: str,
    variables: list[Hit],
    gateway: LLMGateway,
) -> str:
    """A grounded reply, or the research notes if none can be made."""
    notes = research_notes(sql=sql, variables=variables)
    source = "\n".join(
        [
            message,
            prior_question,
            summary,
            sql,
            *[explain.describe_variable(v) for v in variables],
            *notes,
        ]
    )
    feedback: str | None = None
    for _ in range(2):
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[
                Message(
                    role="user",
                    content=build_prompt(
                        message=message,
                        prior_question=prior_question,
                        summary=summary,
                        sql=sql,
                        variables=variables,
                        notes=notes,
                        feedback=feedback,
                    ),
                )
            ],
            temperature=0.2,
            stage="guidance",
        )
        text = explain.plain_text(response.text)
        bad = explain.ungrounded_numbers(text, source)
        if text and not bad:
            return text
        if bad:
            feedback = (
                "Your previous reply contained numbers that are not in the "
                f"material above ({', '.join(bad)}). Rewrite it using only "
                "numbers that appear in the material."
            )
    return fallback(notes)
