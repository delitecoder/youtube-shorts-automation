"""Pronunciation layer: script text -> text the TTS should SPEAK. The script itself is never altered.

Order: dictionary (seed YAML + `Pronunciation` Sheets tab, newest wins; language-specific beats `any`),
then the acronym rule (2-6 capital letters not in `word_acronyms` are spelled out: "AWS" -> "A W S").
`audit(text)` lists the technical terms found and which rule handled each, so reviewers can verify.
"""
from __future__ import annotations

import re

from studio.core import conf

ACRONYM = re.compile(r"(?<![\w.#+-])([A-Z]{2,6})(s?)(?![\w#+])")


def load(store=None, lang: str = "en", cfg: dict | None = None) -> tuple[dict[str, str], set[str]]:
    c = cfg or conf.load("pronunciation")
    rows = list(c.get("entries", []))
    if store is not None:
        try:
            rows += store.rows("Pronunciation")
        except Exception:  # noqa: BLE001 - dictionary tab optional; seed still applies
            pass
    table: dict[str, str] = {}
    for r in rows:                                     # `any` first, then the language overrides it
        if r.get("lang", "any") == "any" and r.get("term") and r.get("spoken"):
            table[r["term"]] = r["spoken"]
    for r in rows:
        if r.get("lang") == lang and r.get("term") and r.get("spoken"):
            table[r["term"]] = r["spoken"]
    return table, set(c.get("word_acronyms", []))


def _term_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w+#])")


def apply(text: str, table: dict[str, str], word_acronyms: set[str]) -> str:
    out = text
    for term in sorted(table, key=len, reverse=True):          # longest first: "PostgreSQL" before "SQL"
        out = _term_re(term).sub(table[term], out)
    return ACRONYM.sub(lambda m: m.group(0) if m.group(1) in word_acronyms else " ".join(m.group(1)) + m.group(2), out)


def audit(text: str, table: dict[str, str], word_acronyms: set[str]) -> list[dict]:
    found = []
    for term in sorted(table, key=len, reverse=True):
        if _term_re(term).search(text):
            found.append({"term": term, "spoken": table[term], "rule": "dictionary"})
    seen = {f["term"] for f in found}
    for m in ACRONYM.finditer(text):
        a = m.group(1)
        if a in seen or any(a in t for t in seen):
            continue
        seen.add(a)
        found.append({"term": a, "spoken": a if a in word_acronyms else " ".join(a),
                      "rule": "word_acronym" if a in word_acronyms else "spelled_acronym"})
    return found
