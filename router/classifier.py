"""Skill Classifier — Step ② (Doc §2).

A lightweight LLM call that compares the user prompt against every SKILL.md
description and picks the best match. The Suadeo doc specifies a threshold
of score ≥ 0.7; below that we fall back to a default skill.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from .skill_loader import SkillRegistry

logger = logging.getLogger(__name__)

FALLBACK_THRESHOLD = 0.7  # Doc §2 row ②
FALLBACK_SKILL = "suadeo-catalogue"  # Safe default — searches the catalogue


class ClassifierOutput(BaseModel):
    """Structured output from the classifier LLM call."""
    skill_name: str = Field(description="Name of the most relevant skill")
    score: float = Field(description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(description="One-sentence reason for the choice")


class ClassificationResult(BaseModel):
    skill_name: str
    score: float
    reasoning: str
    fallback_used: bool


def classify(llm, prompt: str, registry: SkillRegistry) -> ClassificationResult:
    """Run the Skill Classifier against the registry.

    Returns the selected skill name, score, reasoning, and whether a fallback
    was applied because no skill scored ≥ 0.7.
    """
    skills = registry.list_all()
    if not skills:
        raise RuntimeError("No skills loaded — cannot classify")

    # Build system prompt listing every skill name + description
    lines = [
        "You are the Skill Classifier of the Suadeo SDS AI.",
        "Given a user prompt, choose the single most relevant skill from the list below.",
        "Return a confidence score between 0.0 and 1.0.",
        "",
        "Available skills:",
    ]
    for s in skills:
        lines.append(f"- {s.name}: {s.description}")
    system_prompt = "\n".join(lines)

    try:
        structured_llm = llm.with_structured_output(ClassifierOutput)
        output = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ])
    except Exception as e:
        logger.exception("[② classifier] LLM call failed: %s", e)
        return ClassificationResult(
            skill_name=FALLBACK_SKILL,
            score=0.0,
            reasoning=f"classifier error: {e}",
            fallback_used=True,
        )

    # Validate chosen skill exists
    chosen = output.skill_name
    if chosen not in registry.names():
        logger.warning("[② classifier] unknown skill '%s', falling back", chosen)
        return ClassificationResult(
            skill_name=FALLBACK_SKILL if FALLBACK_SKILL in registry.names() else registry.names()[0],
            score=0.0,
            reasoning=f"classifier returned unknown skill '{chosen}'",
            fallback_used=True,
        )

    # Apply score threshold (Doc §2)
    if output.score < FALLBACK_THRESHOLD:
        logger.info("[② classifier] score %.2f < %.2f, falling back to %s",
                    output.score, FALLBACK_THRESHOLD, FALLBACK_SKILL)
        return ClassificationResult(
            skill_name=FALLBACK_SKILL if FALLBACK_SKILL in registry.names() else chosen,
            score=output.score,
            reasoning=output.reasoning,
            fallback_used=True,
        )

    logger.info("[② classifier] %s (score=%.2f)", chosen, output.score)
    return ClassificationResult(
        skill_name=chosen,
        score=output.score,
        reasoning=output.reasoning,
        fallback_used=False,
    )
