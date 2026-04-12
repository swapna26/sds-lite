"""Executor — Steps ④ Tool Planning, ⑤ Tool Execution, ⑥ Aggregation.

This is where the main LLM (gpt-oss-120B in the doc, Gemini here) receives
the skill body as system prompt, is bound to only the tools the skill
declares, and decides which tools to call. We then execute those tools in
parallel and feed the aggregated results back to the LLM for a final answer.

Pattern adapted from:
    langgraph-mcp-agent/agent/graph.py:create_agent_node
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

from .audit import AuditTrail, write_audit
from .skill_loader import Skill, SkillRegistry
from .classifier import classify, ClassificationResult
from tools.registry import ToolRegistry

load_dotenv()
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# LLM factory — matches langgraph-mcp-agent/agent/graph.py:create_llm
# -----------------------------------------------------------------------------

def create_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        google_api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.1,
    )


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------

async def run_pipeline(
    llm: ChatGoogleGenerativeAI,
    user_prompt: str,
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
) -> dict:
    """Run the full 9-step Suadeo flow for a user prompt.

    Returns a dict shaped for the FastAPI response:
        {answer, audit, skill, classifier_reasoning, files?}
    """
    t0 = time.perf_counter()

    # ① User prompt
    logger.info("[① prompt] %s", user_prompt)

    # ② Skill classifier
    classification: ClassificationResult = classify(llm, user_prompt, skill_registry)
    logger.info("[② classifier] %s (score=%.2f, fallback=%s)",
                classification.skill_name, classification.score, classification.fallback_used)

    # ③ SKILL.md loaded
    skill: Skill | None = skill_registry.get(classification.skill_name)
    if skill is None:
        skill = next(iter(skill_registry.list_all()), None)
    if skill is None:
        raise RuntimeError("No skill available to execute")
    logger.info("[③ skill]  %s loaded (%d tools allowed)", skill.name, len(skill.tools))

    # ④ Tool planning — filter tools to the skill's allowlist
    allowed_tools = tool_registry.select(skill.tools)
    system_prompt = _build_system_prompt(skill)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    if allowed_tools:
        llm_with_tools = llm.bind_tools(allowed_tools)
    else:
        llm_with_tools = llm

    try:
        response = await llm_with_tools.ainvoke(messages)
    except Exception as e:
        logger.exception("[④ plan] LLM call failed: %s", e)
        return _error_response(skill, classification, str(e), t0)

    tool_calls = getattr(response, "tool_calls", []) or []
    logger.info("[④ plan]   LLM planned %d tool calls", len(tool_calls))

    # ⑤ Tool execution — parallel
    tools_called: list[str] = []
    tool_results: list[tuple[str, str]] = []  # (lc_name, text)
    if tool_calls:
        coros = []
        for call in tool_calls:
            lc_name = call["name"]
            args = call.get("args", {}) or {}
            qualified = tool_registry.lookup_qualified(lc_name)
            tools_called.append(qualified)
            logger.info("[⑤ exec]   %s(%s)", qualified, _short(args))
            coros.append(_invoke_tool(tool_registry, lc_name, args))

        raw_results = await asyncio.gather(*coros, return_exceptions=True)
        for (call, raw) in zip(tool_calls, raw_results):
            lc_name = call["name"]
            qualified = tool_registry.lookup_qualified(lc_name)
            if isinstance(raw, Exception):
                tool_results.append((qualified, json.dumps({"error": str(raw)})))
            else:
                tool_results.append((qualified, str(raw)))

    # ⑥ Aggregation — feed tool results back to LLM for final answer
    if tool_results:
        logger.info("[⑥ aggregate] merging %d tool results", len(tool_results))
        aggregation_context = "\n\n".join(
            f"## Tool: {name}\n{result}" for name, result in tool_results
        )
        messages.extend([
            AIMessage(content=response.content or "(planned tool calls)"),
            HumanMessage(content=(
                f"Here are the results of the tool calls you requested:\n\n"
                f"{aggregation_context}\n\n"
                f"Please write the final response for the user based on these results. "
                f"Follow the instructions and output format rules from your system prompt."
            )),
        ])
        try:
            final = await llm.ainvoke(messages)
            answer = final.content
        except Exception as e:
            logger.exception("[⑥ aggregate] follow-up LLM failed: %s", e)
            answer = f"(aggregation failed: {e})"
    else:
        answer = response.content or "(no content)"

    # ⑦ Structured generation — we do not enforce a schema here because we
    #    already pass the skill's expected format via the system prompt. An
    #    extension point: let SKILL.md declare a pydantic class path and call
    #    llm.with_structured_output(cls) in the aggregation step above.
    logger.info("[⑦ structure] output length=%d chars", len(answer or ""))

    # ⑧ Backend assembly is performed inside MCP tools (generate_word, etc.)
    #    If the LLM called such a tool, its result (a file path) is already in
    #    tool_results. We surface any file_path fields to the caller.
    files = _extract_files(tool_results)
    if files:
        logger.info("[⑧ assemble] produced files: %s", files)

    # ⑨ Response + audit
    latency_ms = int((time.perf_counter() - t0) * 1000)
    trail = AuditTrail(
        skill_used=skill.name,
        tools_called=tools_called,
        latency_ms=latency_ms,
        classifier_score=classification.score,
        fallback_used=classification.fallback_used,
    )
    write_audit(trail)
    logger.info("[⑨ respond] skill=%s tools=%d latency=%dms",
                skill.name, len(tools_called), latency_ms)

    return {
        "answer": answer,
        "files": files,
        "skill": skill.name,
        "classifier_reasoning": classification.reasoning,
        "audit": trail.to_dict(),
    }


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _build_system_prompt(skill: Skill) -> str:
    """Doc §2 step ③ — inject the SKILL.md body as the system prompt."""
    return (
        f"You are executing the '{skill.name}' skill of the Suadeo SDS AI.\n"
        f"Description: {skill.description}\n\n"
        f"You MUST follow the instructions below:\n\n"
        f"{skill.body}\n\n"
        f"Rules:\n"
        f"- Only call the tools declared by this skill.\n"
        f"- If a tool fails, surface a clear error instead of hallucinating data.\n"
        f"- When producing final output, respect the format described above."
    )


async def _invoke_tool(registry: ToolRegistry, lc_name: str, args: dict) -> str:
    tool = None
    for t in registry.tools.values():
        if t.name == lc_name:
            tool = t
            break
    if tool is None:
        return json.dumps({"error": f"tool not found: {lc_name}"})
    try:
        return await tool.ainvoke(args)
    except Exception as e:
        logger.exception("[⑤ exec] %s failed: %s", lc_name, e)
        return json.dumps({"error": str(e)})


def _short(obj, max_len: int = 80) -> str:
    s = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    return s if len(s) <= max_len else s[:max_len] + "…"


def _extract_files(tool_results: list[tuple[str, str]]) -> list[str]:
    files: list[str] = []
    for _, raw in tool_results:
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict) and "file_path" in obj:
            files.append(obj["file_path"])
        if isinstance(obj, dict) and "file" in obj and isinstance(obj["file"], str):
            files.append(obj["file"])
    return files


def _error_response(skill: Skill, classification: ClassificationResult,
                    error: str, t0: float) -> dict:
    trail = AuditTrail(
        skill_used=skill.name,
        tools_called=[],
        latency_ms=int((time.perf_counter() - t0) * 1000),
        classifier_score=classification.score,
        fallback_used=classification.fallback_used,
    )
    write_audit(trail)
    return {
        "answer": f"Pipeline error: {error}",
        "files": [],
        "skill": skill.name,
        "classifier_reasoning": classification.reasoning,
        "audit": trail.to_dict(),
    }
