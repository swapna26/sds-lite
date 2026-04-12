"""FastAPI Skill Router — sds-lite entry point.

Implements the operational surface of the Suadeo SDS AI architecture
(Doc §8.3) as a FastAPI app:

    POST /chat             → Run the 9-step pipeline on a user prompt   (Doc §2)
    POST /skills/reload    → Hot reload SKILL.md files                  (Doc §3.3)
    GET  /skills           → List loaded skills
    GET  /tools            → List discovered MCP + agent tools
    POST /mcp/register     → Runtime-register a new MCP server          (Doc §6.4)
    GET  /health           → Liveness
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from router.classifier import FALLBACK_THRESHOLD
from router.executor import create_llm, run_pipeline
from router.skill_loader import SkillRegistry
from tools.mcp_gateway import MCPGateway
from tools.registry import ToolRegistry

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("sds-lite")

PROJECT_DIR = Path(__file__).parent
SKILLS_DIR = PROJECT_DIR / "skills"

# Global state populated during lifespan startup
llm = None
skill_registry: SkillRegistry | None = None
tool_registry: ToolRegistry | None = None
gateway: MCPGateway | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to MCP servers, load skills. Shutdown: close connections."""
    global llm, skill_registry, tool_registry, gateway

    if not os.getenv("GEMINI_API_KEY"):
        logger.warning("GEMINI_API_KEY not set — pipeline calls will fail until you set it in .env")

    logger.info("═══ sds-lite startup ═══")

    # Pillar 1 — load skills
    skill_registry = SkillRegistry(SKILLS_DIR)
    skill_registry.load_all()
    logger.info("Skills loaded: %s", skill_registry.names())

    # Pillar 2 + 3 — connect to MCP servers via the Gateway
    gateway = MCPGateway(project_dir=PROJECT_DIR)
    await gateway.connect_all()

    # Build the tool registry (agent tools + gateway meta + MCP namespaced tools)
    tool_registry = ToolRegistry(gateway)
    tool_registry.build()
    logger.info("Tools registered: %d", len(tool_registry.tools))

    # LLM
    llm = create_llm()
    logger.info("LLM initialized: %s", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

    logger.info("═══ sds-lite ready — POST /chat to start ═══")
    yield

    # Shutdown
    if gateway:
        await gateway.close_all()
    logger.info("sds-lite shutdown complete")


app = FastAPI(
    title="sds-lite",
    description="Python implementation mirroring the Suadeo SDS AI Architecture",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------

class ChatRequest(BaseModel):
    query: str = Field(..., description="User's natural-language prompt")


class ChatResponse(BaseModel):
    answer: str
    skill: str
    classifier_reasoning: str
    files: list[str] = []
    audit: dict


class MCPRegisterRequest(BaseModel):
    name: str
    url: str
    auth: str = ""
    transport: str = "http"


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "skills_loaded": len(skill_registry.list_all()) if skill_registry else 0,
        "tools_registered": len(tool_registry.tools) if tool_registry else 0,
        "classifier_threshold": FALLBACK_THRESHOLD,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not (llm and skill_registry and tool_registry):
        raise HTTPException(status_code=503, detail="Router not initialised")
    result = await run_pipeline(llm, req.query, skill_registry, tool_registry)
    return ChatResponse(**result)


@app.get("/skills")
async def list_skills():
    if not skill_registry:
        return {"skills": []}
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "tools": s.tools,
                "file": str(s.file_path.relative_to(PROJECT_DIR)) if s.file_path else "",
            }
            for s in skill_registry.list_all()
        ]
    }


@app.post("/skills/reload")
async def reload_skills():
    """Hot reload — the Suadeo 'golden rule' from Doc §3.3."""
    if not skill_registry:
        raise HTTPException(status_code=503, detail="Skill registry not initialised")
    count = skill_registry.reload()
    return {"status": "ok", "skills_loaded": count, "names": skill_registry.names()}


@app.get("/tools")
async def list_tools():
    if not tool_registry:
        return {"tools": []}
    return {
        "count": len(tool_registry.tools),
        "qualified_names": tool_registry.list_qualified_names(),
    }


@app.post("/mcp/register")
async def mcp_register(req: MCPRegisterRequest):
    """Register a new MCP server at runtime (Doc §6.4)."""
    if not gateway or not tool_registry:
        raise HTTPException(status_code=503, detail="Gateway not initialised")
    result = await gateway.mcp_register(req.name, req.url, req.auth, req.transport)
    # Rebuild the tool registry so the new tools become available to skills
    tool_registry.build()
    return {"gateway_result": result, "tools_after": len(tool_registry.tools)}
