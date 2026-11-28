"""Editorial policy gates (rules.md §1-§3), enforced in code.

topic_gate: technology education only; entertainment/celebrity/trailer/sports/politics topics are rejected
unless the research states a substantial technology lesson (`tech_lesson`, >= 12 words naming the concept).
scene_contract: storyboard schema 2 - every scene states its narrative purpose and visual purpose, and a
scene that states a number or a factual comparison cites a researched claim.
"""
from __future__ import annotations

import re

from studio.core import conf

OFF_TOPIC = re.compile(r"\b(trailer|teaser|box office|celebrity|actor|actress|movie|film release|bollywood|kollywood|"
                       r"hollywood|cricket|football|ipl|election|politic\w*|gossip|meme|viral video|reality show)\b", re.I)
NUMERIC_CLAIM = re.compile(r"\d|percent|times faster|times slower|billion|million|thousand")
STRUCTURES = ("problem_mechanism_solution", "how_it_works", "inside_architecture", "investigation", "case_study",
              "myth_vs_reality", "comparison", "timeline", "step_by_step", "evidence_future")


def topic_gate(research: dict, cfg: dict | None = None) -> list[str]:
    c = cfg or conf.load("app")
    text = " ".join(str(research.get(k, "")) for k in ("topic", "angle", "category"))
    errors = []
    if OFF_TOPIC.search(text):
        lesson = str(research.get("tech_lesson", ""))
        if len(lesson.split()) < 12:
            errors.append("off-topic for a technology channel (entertainment/celebrity/sports/politics): state a "
                          "substantial technology lesson in 'tech_lesson' (>= 12 words) or pick another topic")
    domains = [d.lower() for d in c["editorial"]["allowed_domains"]]
    if research.get("domain") and str(research["domain"]).lower() not in domains:
        errors.append(f"domain {research['domain']!r} not in allowed domains")
    return errors


def scene_contract(sb: dict) -> tuple[list[str], list[str]]:
    """Errors for schema>=2 storyboards, warnings for legacy ones (so old work stays renderable)."""
    strict = int(sb.get("schema", 1)) >= 2
    issues: list[str] = []
    if strict and sb.get("story_structure") not in STRUCTURES:
        issues.append(f"story_structure must be one of {STRUCTURES}")
    claim_ids = {c.get("id") for c in sb.get("claims", []) if isinstance(c, dict)}
    for i, s in enumerate(sb.get("scenes", []), 1):
        if s.get("auto") or s.get("outro"):                # automatic outro / subscribe card
            continue
        for k in ("purpose", "visual_purpose"):
            if len(str(s.get(k, "")).split()) < 3:
                issues.append(f"scene {i}: '{k}' missing (state why this scene exists in >= 3 words)")
        if NUMERIC_CLAIM.search(s.get("narration", "")) and not (set(s.get("claims", [])) & claim_ids):
            issues.append(f"scene {i}: states a number/factual comparison but cites no researched claim id")
    return (issues, []) if strict else ([], [f"legacy storyboard (schema 1): {x}" for x in issues[:5]])


def research_contract(r: dict) -> tuple[list[str], list[str]]:
    """Each fact: status (verified|estimate|opinion|speculation); high-impact facts need independent verification;
    each source an access date. Errors only for schema>=2 research JSON."""
    strict = int(r.get("schema", 1)) >= 2
    issues = []
    for f in r.get("facts", []):
        if f.get("status") not in ("verified", "estimate", "opinion", "speculation"):
            issues.append(f"fact '{str(f.get('claim', ''))[:50]}': status must be verified|estimate|opinion|speculation")
        if f.get("high_impact") and not f.get("verified_by"):
            issues.append(f"high-impact fact '{str(f.get('claim', ''))[:50]}' needs research-verifier sign-off (verified_by)")
        if strict and not f.get("id"):
            issues.append(f"fact '{str(f.get('claim', ''))[:50]}' needs an id (scenes cite it)")
    for s in r.get("sources", []):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(s.get("accessed", ""))):
            issues.append(f"source {s.get('url')}: 'accessed' date (YYYY-MM-DD) missing")
    return (issues, []) if strict else ([], [f"legacy research (schema 1): {x}" for x in issues[:5]])
