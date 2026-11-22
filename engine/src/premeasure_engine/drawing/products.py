"""Suggest which Callidus material matches each legend finish.

Architects and Crean describe the same product differently ("FLOOR FINISH
TYPE 13: ... MARMOLEUM CONCRETE. COLOUR NEPTUNE 3756" against "FF8 - Forbo
Concrete Neptune 3756"), and the numbering differs. Product codes and colour
names are what they share, so those are matched. This only suggests: the
estimator confirms the mapping during project setup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STOP = {
    "THICKNESS", "COLOUR", "COLOR", "FORBO", "ALTRO", "FLOORING", "CLASS", "SLIP", "RESISTANCE", "SAFETY",
    "MARMOLEUM", "CONCRETE", "FRESCO", "WALTON", "AQUARIUS", "CLASSIC", "WITH", "FROM", "TYPE", "FLOOR",
    "FINISH", "THE", "AND", "TO", "OR", "EQUIVALENT", "GRIP", "RANGE",
}


def _tokens(text: str) -> tuple[set[str], set[str]]:
    t = text.upper()
    codes = set(re.findall(r"\b[A-Z]*\d{4,}[A-Z0-9]*\b", t))
    words = {w for w in re.findall(r"[A-Z]{4,}", t) if w not in STOP}
    return codes, words


@dataclass
class Suggestion:
    legend_code: str
    material: str | None
    score: float
    why: str


def _norm(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def suggest_materials(entries, material_names: list[str]) -> list[Suggestion]:
    out = []
    siblings: dict[str, list[set[str]]] = {}
    for e in entries:
        b = _norm(e.code.split(" (")[0])
        siblings.setdefault(b, []).append(_tokens(e.description)[1])
    for e in entries:
        codes, words = _tokens(e.description)
        base = _norm(e.code.split(" (")[0])
        shared = len(siblings.get(base, [])) > 1
        if shared:
            # one code, several colours: only the words that tell the siblings apart count
            words = words - set.intersection(*siblings[base])
        best = (0.0, None, "")
        for name in material_names:
            mc, mw = _tokens(name)
            shared_codes = codes & mc
            # also allow a code that starts with the other (386635 vs 3866)
            loose = {a for a in codes for b in mc if a != b and (a.startswith(b) or b.startswith(a)) and min(len(a), len(b)) >= 4}
            shared_words = words & mw
            score = 3 * len(shared_codes) + 1.5 * len(loose) + len(shared_words)
            lead = _norm(name.split(" ", 1)[0])
            if e.custom_code and lead == base and (not shared or shared_words):
                # the drawing's own finish code starts the material name (F1-E / "F1E - ...")
                score += 6
            elif e.custom_code and lead == base and shared:
                score = 0  # same code, different colour: never pick on the code alone
            if score > best[0]:
                why = ", ".join(sorted(shared_codes | loose | shared_words))
                best = (score, name, why)
        out.append(Suggestion(e.code, best[1], best[0], best[2]))
    return out
