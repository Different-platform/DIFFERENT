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
