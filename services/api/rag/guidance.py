"""Answer questions about numbers that were already worked out.

"How can I use this in my research?", "why did BMI go up from 2022 to 2023?",
and "is that a big difference?" are questions about results already shown.
The useful answer depends on facts about those results: that the survey
records what people reported but not why, that a small difference between
years can be chance, that BMI came from self-reported height and weight,
that no margin of error was calculated. Those facts are written here as fixed
notes built from the saved SQL and the codebook, so the cautions are true for
the number and do not depend on the model remembering them.

The model turns the notes and the conversation so far into a short,
plain-language answer to the question actually asked. The same number check
as rag/explain.py applies: every number in the reply must appear in the
material it was given, so it cannot compute a new difference or invent a
cause. A reply that fails twice falls back to the notes themselves.
"""

from __future__ import annotations

import re

from services.api.domain import Hit
from services.api.llm.gateway import LLMGateway, Message
from services.api.rag import derivations, explain
from services.api.rag.style import PLAIN_LANGUAGE, style_problems

SYSTEM_PROMPT = """\
You answer a follow-up question about numbers you already worked out from the \
U.S. National Health Interview Survey (NHIS). The reader may be a student, a \
journalist, a member of the public, or a researcher.

You are given their message, the earlier messages in this conversation (which \
contain the numbers already shown), the most recent number's question, summary \
and SQL, codebook details, and notes that are true for these numbers.

Answer what they actually asked, starting in the first sentence.
- If they ask WHY a number changed or differs: say plainly that the survey \
shows what people reported, not the reasons. Give the numbers already shown \
for each year or group being compared, and say whether the difference looks \
small. Explain that a small difference can happen by chance. Do not guess at \
causes.
- If they ask how to USE the number, for research or otherwise: say what it is \
good for (describing U.S. adults, background for a study or article, comparing \
with other published figures) and what it cannot do on its own (prove a cause, \
or show that a difference is real).
- Do not give citation details, name survey software, or name design \
variables: a fixed sentence for researchers is added after your answer when \
it is relevant.
- Otherwise, answer the message directly from the material.
- Use only the notes that matter for this message, usually two or three.

Rules:
- Use only facts in the material provided. Do not state any number that is \
not in it. Do not calculate new numbers such as differences or percentage \
changes; say "slightly higher" or "about the same" instead.
- The statistics WERE computed. Never say they cannot be computed.
- State each caution exactly as strongly as the notes do; do not widen it.

""" + PLAIN_LANGUAGE

#: How much of the conversation to show the model: the last few exchanges
#: hold the numbers a "why did it change" question is about.
EARLIER_MESSAGES = 6

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
    """True statements about this number, most widely useful first.

    Built only from the saved SQL and codebook details, so each note can be
    checked against what actually ran. The last note is the technical one, for
    a researcher writing a methods section.
    """
    weight = _weight_of(sql)
    year = explain.year_of(sql)
    names = explain.variable_names(sql)
    notes: list[str] = []

    notes.append(
        "The survey records what people reported when they were interviewed. "
        "It does not record the reasons behind a number, so on its own it "
        "cannot explain why a figure went up or down, or what caused it."
    )
    notes.append(
        "Each year the survey interviews a different sample of people, so a "
        "small difference between two years or two groups can happen by "
        "chance. No margin of error was calculated here, so this tool cannot "
        "say whether a small difference is real."
    )

    if _bmi_was_derived(sql):
        notes.append(
            "BMI was worked out from the height and weight people reported "
            "themselves (703 x weight in pounds, divided by height in inches "
            "squared). People tend to report a little less weight and a little "
            "more height than they really have, so BMI from self-reports "
            "usually comes out lower than BMI from measured height and weight."
        )

    when = f"in {year}" if year is not None else "in that year"
    notes.append(
        f"The number is adjusted with the survey's weights so it represents "
        f"U.S. adults living in households {when}, not only the people "
        f"interviewed. It is a snapshot of that year; the survey does not "
        f"follow the same people over time."
    )

    missing: list[str] = []
    for hit in variables:
        for label in hit.sentinel_labels:
            text = (label.label or "").strip().lower()
            if text and text not in missing:
                missing.append(text)
    reasons = f" ({', '.join(missing)})" if missing else ""
    notes.append(
        f"People with no usable answer{reasons} were left out, so the number "
        f"describes adults who gave a usable answer."
    )

    notes.append(
        "Survey years 2019 to 2023 use the same questionnaire, so they can "
        "generally be compared after checking that the question was asked the "
        "same way each year. Years before 2019 used an older questionnaire and "
        "are not directly comparable. In 2020 most interviews were done by "
        "telephone because of the COVID-19 pandemic, so 2020 figures need "
        "extra caution."
    )

    source = (
        f"the National Health Interview Survey {year} Sample Adult public-use "
        f"file from the National Center for Health Statistics"
        if year is not None
        else "the National Health Interview Survey Sample Adult public-use "
        "file from the National Center for Health Statistics"
    )
    used = f", naming the variables used ({', '.join(names)})" if names else ""
    notes.append(
        f"For a paper: cite {source}{used}. To add a margin of error or test a "
        f"difference, re-run the estimate in survey software (for example the "
        f"R survey package, Stata's svy commands, or SAS PROC SURVEYMEANS) with "
        f"strata PSTRAT, clusters PPSU and weight {weight}."
    )
    return notes


#: Wording that asks how to use, report, or cite a number. Only then is the
#: technical note (survey file, PSTRAT, PPSU) relevant; a "why did it go up?"
#: answer that ends with design-variable names is the jargon this replaces.
_USE_CUE = re.compile(
    r"\b(?:use|using|research|study|studies|paper|thesis|dissertation|cite|"
    r"citation|report|publish\w*|article|analys[ie]s|methods?|journal|"
    r"manuscript|limitations?)\b",
    re.IGNORECASE,
)


def notes_for(message: str, notes: list[str]) -> list[str]:
    """The notes relevant to `message`: the methods note only when asked."""
    if _USE_CUE.search(message or ""):
        return notes
    return notes[:-1]


def _split_methods(notes: list[str]) -> tuple[list[str], str | None]:
    """Separate the methods note, which is appended verbatim, not paraphrased.

    A live reply turned "re-run it with PSTRAT and PPSU to get a margin of
    error" into "the estimate was produced with PSTRAT and PPSU", which is
    false. Technical instructions are exact or absent.
    """
    if notes and notes[-1].startswith("For a paper:"):
        return notes[:-1], notes[-1]
    return notes, None


def fallback(notes: list[str]) -> str:
    """Used when no grounded reply can be produced.

    The three most widely useful notes plus the one for researchers, rather
    than all of them: a wall of caveats is the unreadable answer this module
    exists to replace.
    """
    last = notes[-1] if len(notes) > 3 and notes[-1].startswith("For a paper:") else None
    chosen = notes[:3] + ([last] if last else [])
    return "Here is what this number can and cannot tell you.\n\n" + "\n\n".join(chosen)


def _earlier_text(earlier: list[Message]) -> str:
    lines = [
        f"{'Reader' if m.role == 'user' else 'Assistant'}: {m.content}"
        for m in earlier[-EARLIER_MESSAGES:]
    ]
    return "\n\n".join(lines) or "(none)"


def build_prompt(
    *,
    message: str,
    prior_question: str,
    summary: str,
    sql: str,
    variables: list[Hit],
    notes: list[str],
    earlier: list[Message],
    feedback: str | None = None,
) -> str:
    details = "\n".join(explain.describe_variable(v) for v in variables) or "(none)"
    prompt = (
        f"Their message: {message}\n\n"
        f"Earlier in this conversation:\n{_earlier_text(earlier)}\n\n"
        f"The most recent number answered: {prior_question}\n\n"
        f"Summary already given for it:\n{summary}\n\n"
        f"The exact SQL that ran:\n{sql}\n\n"
        f"Codebook details for the variables it used:\n{details}\n\n"
        f"Notes (true for these numbers):\n"
        + "\n".join(f"- {note}" for note in notes)
        + "\n\nAnswer their message."
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
    earlier: list[Message] | None = None,
) -> str:
    """A grounded reply, or the most useful notes if none can be made."""
    earlier = list(earlier or [])
    all_notes = research_notes(sql=sql, variables=variables)
    notes, methods = _split_methods(notes_for(message, all_notes))

    def finish(text: str) -> str:
        return f"{text}\n\n{methods}" if methods else text

    source = "\n".join(
        [
            message,
            prior_question,
            summary,
            sql,
            _earlier_text(earlier),
            *[explain.describe_variable(v) for v in variables],
            *notes,
        ]
    )
    feedback: str | None = None
    candidate: str | None = None
    for attempt in range(2):
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
                        earlier=earlier,
                        feedback=feedback,
                    ),
                )
            ],
            temperature=0.2,
            stage="guidance",
        )
        text = explain.plain_text(response.text)
        bad = explain.ungrounded_numbers(text, source)
        wording = style_problems(text)
        # Ungrounded numbers are never accepted. Wording problems get one
        # retry; a grounded but wordy reply still beats the fallback.
        if text and not bad:
            if not wording or attempt == 1:
                return finish(text)
            candidate = text
        problems: list[str] = []
        if bad:
            problems.append(
                "Your previous reply contained numbers that are not in the "
                f"material above ({', '.join(bad)}). Rewrite it using only "
                "numbers that appear in the material, and describe any "
                "difference in words instead of calculating it."
            )
        problems.extend(wording)
        feedback = " ".join(problems) or None
    if candidate:
        return finish(candidate)
    return fallback(notes + ([methods] if methods else []))
