"""How every answer is worded: plain language first.

DIFFER is used by people who are not statisticians. An answer that opens with
"a cross-sectional point estimate for U.S. civilian noninstitutionalized
adults" is accurate and useless to them. The technical material is not lost:
the variable codes are on the citation cards, the exact query is in the SQL
panel, and the weight variable is on the result card. The prose only has to
say what the answer means.

One shared instruction block so every model-written answer reads the same way.
"""

from __future__ import annotations

import re

PLAIN_LANGUAGE = """\
How to write (the reader may have no statistics, survey, or programming \
background):
- Answer the question in the first sentence, in everyday words.
- Use short sentences. If you need a technical term, explain it in a few \
plain words the first time.
- Do not use jargon such as "point estimate", "cross-sectional", \
"population-weighted", "noninstitutionalized", "sentinel", "universe", \
"strata", "PSU", "derived", or "inference". Say what it means instead.
- Do not write SQL. Keep variable codes (like HEIGHTTC_A) out of the prose \
unless the rules below allow them; the reader can see them on the cards above \
your answer.
- Be brief: at most two short paragraphs, about 120 words in total.
- No headings, no bullet lists, no markdown. Separate paragraphs with a blank \
line.\
"""

#: Typographic characters the model emits that look like ordinary ones but
#: break copy-paste into a paper and text search, and in some fonts render as
#: no space at all ("18\u202fyears" reads as "18years").
_LOOK_ALIKES = str.maketrans(
    {
        "\u00a0": " ",  # no-break space
        "\u202f": " ",  # narrow no-break space
        "\u2009": " ",  # thin space
        "\u2007": " ",  # figure space
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
    }
)


def plain_chars(text: str) -> str:
    """Replace look-alike spaces and hyphens with ordinary ones.

    Safe on a streamed chunk: it maps one character to one character and
    never trims, so chunk boundaries are unaffected.
    """
    return (text or "").translate(_LOOK_ALIKES)


#: Terms the plain-language rules forbid. The prompt alone does not stop the
#: model using them (a live reply said "non-institutionalized" and
#: "descriptive statistic"), so a reply is checked and retried once.
_JARGON = re.compile(
    r"\b(?:point estimates?|cross[- ]sectional|population[- ]weighted|"
    r"weighted (?:mean|average)|non[- ]?institutionali[sz]ed|descriptive statistics?|"
    r"sampling variation|sentinel|inference|inferential|statistically significant)\b",
    re.IGNORECASE,
)

#: Above this a reply gets one retry asking for a shorter answer.
MAX_WORDS = 150


def style_problems(text: str) -> list[str]:
    """Why `text` breaks the plain-language rules (empty when it does not)."""
    problems: list[str] = []
    terms = sorted({m.group(0).lower() for m in _JARGON.finditer(text or "")})
    if terms:
        problems.append(
            "It used technical terms a non-specialist will not know ("
            + ", ".join(terms)
            + "). Say what they mean in everyday words instead."
        )
    words = len((text or "").split())
    if words > MAX_WORDS:
        problems.append(
            f"It was {words} words long. Keep it under {MAX_WORDS - 30} words."
        )
    return problems


#: Last-resort plain wording for terms the model keeps using after a retry.
#: Each swap keeps the meaning and is safe anywhere in a sentence.
_SWAPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:civilian,?\s+)?non-?institutionali[sz]ed\s+", re.IGNORECASE), ""),
    (re.compile(r"\bweighted (mean|average)\b", re.IGNORECASE), "average"),
    (re.compile(r"\bpopulation[- ]weighted\s+", re.IGNORECASE), ""),
    (re.compile(r"\bsampling variation\b", re.IGNORECASE), "random variation"),
    (re.compile(r"\bdescriptive statistic\b", re.IGNORECASE), "summary number"),
    (re.compile(r"\bpoint estimate\b", re.IGNORECASE), "estimate"),
)


def simplify(text: str) -> str:
    """Swap a few stubborn technical terms for everyday ones."""
    for pattern, replacement in _SWAPS:
        text = pattern.sub(replacement, text)
    return text
