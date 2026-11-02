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
