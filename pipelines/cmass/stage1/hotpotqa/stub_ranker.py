"""A trivial stand-in ranker for the offline demo of rank_screen.py: NOT the ranker that was run.

judge(fact, fact_about, document) returns the share of the fact's content words that occur in the
document as "states_fact", and whether the document names the fact's subject (its title words) as
"same_entity". It costs nothing and needs no network. The ranker that was run (Jev, a hosted model
returning calibrated probabilities) is described in README.md, "The ranking step"; any replacement
only has to keep this function's signature and return fields.
"""
import re

STOP = set("""a an the of in on at to for from by with and or but is are was were be been being as that this
these those it its his her their there which who whom whose what when where how why did do does has have had
not no also into over under after before than then""".split())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in STOP}


def judge(fact: str, fact_about: str, document: str) -> dict:
    doc = _words(document)
    fw, tw = _words(fact), _words(fact_about)
    states = len(fw & doc) / len(fw) if fw else 0.0
    same = 1.0 if tw and tw <= doc else 0.0
    return {"states_fact": round(states, 2), "same_entity": same, "grade": round(3 * states), "cost": 0.0}
