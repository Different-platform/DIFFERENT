"""Answer a follow-up about codebook variables already cited.

Mirrors `rag/explain.py`'s discipline — grounded in material that was already
shown, checked before it is trusted — applied to codebook citations instead
of a computed statistic.

`explain.py` checks that every *number* in its reply already appeared in the
material it was given, because the product's promise is "no number unless
verified". The equivalent risk here is a *variable name*: a reply that names
a variable not already cited has quietly gone back to open-ended retrieval
and could be describing something the researcher never asked about. So this
module checks variable names the same way `explain.py` checks numbers — a
reply naming an uncited variable is never accepted; it gets one retry naming
the problem, then falls back to literally restating what was already cited.

No retrieval and no presence judge run here. The only variables this module
can ever discuss are the ones already in `variables` — which is what makes it
structurally impossible to repeat the RJWRKCLSSD_A / RJCLSSD1_A drift: the
model is simply never shown RJCLSSD1_A when the citation was RJWRKCLSSD_A.
"""

from __future__ import annotations

import re

from services.api.domain import Citation, Hit
from services.api.llm.gateway import LLMGateway, Message
from services.api.rag.explain import describe_variable, plain_text
from services.api.rag.generate import extract_variable_mentions
from services.api.rag.style import PLAIN_LANGUAGE, simplify, style_problems

SYSTEM_PROMPT = """\
A health researcher is chatting with an assistant about the U.S. National \
Health Interview Survey (NHIS) codebook. The assistant already named one or \
more specific variables in its last answer. Answer the researcher's follow-up \
about those variables.

You are given the earlier question, the assistant's previous answer, and \
codebook details for the variable(s) it named. Use only those variables:
- Never name a variable that is not in the codebook details provided.
- You MAY compare, rank, or give an opinion about the variables provided \
(for example, which is more commonly used, or why one does not fit) — that \
is reasoning about what you already said, not a new coverage claim.
- Do not claim NHIS measures or does not measure a concept; that was already \
decided and is not being revisited here.
- If the researcher's question needs a variable that was not already named, \
say plainly that you would need to look that up rather than guessing.

""" + PLAIN_LANGUAGE

#: Pure metadata the follow-up can be answered from directly, no LLM call and
#: no risk of drift. Narrow on purpose: anything asking for *why* or *which*
#: falls through to the reasoning path below, where an opinion is the point.
_PAGE_CUE = re.compile(r"\bwhat page\b", re.IGNORECASE)
_YEAR_CUE = re.compile(r"\bwhat year\b", re.IGNORECASE)
_SECTION_CUE = re.compile(r"\bwhat section\b", re.IGNORECASE)


def metadata_answer(citations: list[Citation], question: str) -> str | None:
    """Answer a pure metadata question straight from the citation records.

    Returns None when the question is not one of these exact shapes, so the
    caller falls through to the reasoning path.
    """
    if not citations:
        return None
    if _PAGE_CUE.search(question):
        parts = [f"{c.variable_name} is on page {c.page} ({c.survey_year})." for c in citations]
        return " ".join(parts)
    if _YEAR_CUE.search(question):
        parts = [f"{c.variable_name} is from the {c.survey_year} codebook." for c in citations]
        return " ".join(parts)
    if _SECTION_CUE.search(question):
        parts = [f"{c.variable_name} is in the {c.section} section." for c in citations]
        return " ".join(parts)
    return None


def _cited_names(variables: list[Hit]) -> set[str]:
    return {v.variable_name for v in variables}


def ungrounded_variables(text: str, cited: set[str]) -> list[str]:
    """Variable-looking names in `text` that were not among the ones cited."""
    mentioned = extract_variable_mentions(text)
    return sorted(name for name in mentioned if name not in cited)


def build_prompt(
    question: str,
    prior_question: str,
    prior_answer: str,
    variables: list[Hit],
    feedback: str | None = None,
) -> str:
    details = "\n".join(describe_variable(v) for v in variables) or "(none)"
    prompt = (
        f"Earlier question: {prior_question}\n\n"
        f"Assistant's previous answer:\n{prior_answer}\n\n"
        f"Codebook details for the variable(s) named:\n{details}\n\n"
        f"Researcher's follow-up: {question}"
    )
    if feedback:
        prompt += f"\n\n{feedback}"
    return prompt


def fallback(prior_answer: str) -> str:
    """Used when no grounded reply can be produced."""
    return (
        f"{prior_answer}\n\nThat is what I can say about the variable(s) named "
        f"above without looking up anything new."
    )


async def compose(
    *,
    question: str,
    prior_question: str,
    prior_answer: str,
    variables: list[Hit],
    gateway: LLMGateway,
) -> str:
    """A grounded reply, or the prior answer restated if none can be made."""
    cited = _cited_names(variables)
    feedback: str | None = None
    candidate: str | None = None
    for attempt in range(2):
        response = await gateway.complete(
            system=SYSTEM_PROMPT,
            messages=[
                Message(
                    role="user",
                    content=build_prompt(
                        question, prior_question, prior_answer, variables, feedback
                    ),
                )
            ],
            temperature=0.2,
            stage="cite_explain",
        )
        text = plain_text(response.text)
        bad = ungrounded_variables(text, cited)
        wording = style_problems(text)
        if text and not bad:
            if not wording or attempt == 1:
                return simplify(text)
            candidate = text
        notes: list[str] = []
        if bad:
            notes.append(
                "Your previous reply named variable(s) that were not in the "
                f"codebook details given ({', '.join(bad)}). Rewrite it using "
                "only the variable(s) already named."
            )
        notes.extend(wording)
        feedback = " ".join(notes) or None
    return simplify(candidate) if candidate else fallback(prior_answer)
