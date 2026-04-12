# sds-lite

A Python implementation that mirrors the **Suadeo SDS AI Architecture** (see `Suadeo_SDS_AI_Architecture_EN.docx`) as a learning project.

Every structural element of the document is preserved:
- **3 pillars**: Skills, Tools, MCP Server
- **9-step flow** from user prompt to final response
- **Same skill names** (`suadeo-dashboard`, `suadeo-etl`, `suadeo-catalogue`, `suadeo-report`, `suadeo-code-review`, `suadeo-test-data`)
- **Same 15 MCP tools** across Catalogue, ETL, Governance
- **Same MCP Gateway** (`mcp_list_tools`, `mcp_call`, `mcp_register`)
- **Same audit trail** fields

## Deliberate simplifications (where the "lite" kicks in)

| Doc says | sds-lite does | Why |
|---|---|---|
| C# .NET 10 MCP Server | Python FastMCP server | Keep single language for learning |
| `gpt-oss-120B` via vLLM | Google Gemini `gemini-2.5-flash` | Matches existing `langgraph-mcp-agent/` setup |
| Real Suadeo platform endpoints | Mock data in `mock_data/` | Runs end-to-end without the real platform |
| HTTP/SSE MCP transport | stdio | Simpler for local dev — same protocol semantics |

## Architecture → File map

```
sds-lite/
├── app.py                       # FastAPI Skill Router (Doc §8.3)
├── mcp_registry.json            # MCP server registry (Doc §6.3)
│
├── skills/                      # PILLAR 1 — SKILL.md files (Doc §3)
│   ├── suadeo-dashboard.md
│   ├── suadeo-etl.md
│   ├── suadeo-catalogue.md
│   ├── suadeo-report.md
│   ├── suadeo-code-review.md
│   └── suadeo-test-data.md
│
├── router/                      # PILLAR 1 runtime
│   ├── skill_loader.py          # Parses SKILL.md (Doc §3.2)
│   ├── classifier.py            # Step ② — score ≥ 0.7
│   ├── executor.py              # Steps ④–⑦
│   └── audit.py                 # Step ⑨
│
├── tools/                       # PILLAR 2a — agent tools (Doc §4.2)
│   ├── web_search.py
│   ├── web_fetch.py
│   ├── read_document.py
│   ├── get_user_context.py
│   ├── analyze_code_quality.py
│   ├── generate_chart.py
│   └── mcp_gateway.py           # The universal connector (Doc §6)
│
├── servers/                     # PILLAR 3 — MCP Servers (Doc §5)
│   ├── suadeo_sds_server.py     # 15 tools
│   └── gitlab_server.py         # Mock GitLab (for Doc §7.2)
│
├── assemblers/                  # Step ⑧ — real file generation
│   ├── word_assembler.py        # python-docx
│   ├── excel_assembler.py       # openpyxl
│   └── dashboard_assembler.py
│
├── mock_data/                   # Fake Suadeo platform data
└── outputs/                     # Generated .docx / .xlsx / audit.log
```

## Quick start

```bash
cd ~/Documents/Personal/sds-lite
cp .env.example .env
# edit .env — add your GEMINI_API_KEY

uv sync
uv run uvicorn app:app --reload --port 8090
```

## Try it

```bash
# 1. List the 6 skills
curl http://localhost:8090/skills

# 2. List discovered MCP tools (15 suadeo.* + mock gitlab.*)
curl http://localhost:8090/tools

# 3. Doc §7.1 — Generate a Q1 sales Word report
curl -X POST http://localhost:8090/chat \
     -H 'Content-Type: application/json' \
     -d '{"query":"Generate a Word report of Q1 2026 sales by region"}'

# 4. Doc §7.2 — GitLab code review
curl -X POST http://localhost:8090/chat \
     -H 'Content-Type: application/json' \
     -d '{"query":"Analyze the GitLab commits from the last 24 hours and produce a Word report"}'

# 5. Create a dashboard
curl -X POST http://localhost:8090/chat \
     -H 'Content-Type: application/json' \
     -d '{"query":"Create a DevExpress dashboard for dataset sales_q1_2026"}'

# 6. Hot reload skills (Doc §3.3 "golden rule")
#    Drop a new .md file in skills/ then:
curl -X POST http://localhost:8090/skills/reload
```

## Watching the 9-step flow

Run the server with `--log-level info` and every request emits labelled steps:

```
[① prompt]     user query received
[② classifier] suadeo-report  score=0.91
[③ skill]      suadeo-report.md loaded (3 tools allowed)
[④ plan]       LLM planned 3 tool calls
[⑤ exec]       suadeo.get_schema(...)
[⑤ exec]       suadeo.execute_query(...)
[⑤ exec]       web_search(...)
[⑥ aggregate]  merging 3 tool results
[⑦ structure]  JSON schema validated
[⑧ assemble]   writing outputs/Q1_sales_report.docx
[⑨ respond]    audit: skill=suadeo-report, tools=4, latency=2341ms
```

Read the Suadeo architecture document side-by-side with this log and the mapping is 1:1.
