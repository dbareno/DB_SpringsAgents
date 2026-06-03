# Spring Design Agent 🌀

**Agentic multi-type spring design system powered by LangGraph and FastAPI.**

Accepts natural-language requirements — from precise specs to vague descriptions — and returns fully engineered spring designs validated against DIN/ASTM standards, with commercial scoring and Three.js-ready geometry parameters.

---

## ✨ Features

- **Natural-language input** — "I need a small spring for my mechanical pen" or full engineering specs
- **6-agent LangGraph pipeline** — requirements extraction → material selection → geometry optimization → compliance verification → commercial scoring
- **Multi-LLM fallback** — Gemini → Grok → OpenAI → Claude → Ollama (local), rotating automatically on quota errors
- **SciPy optimization** — `scipy.optimize.minimize` solves for minimum wire volume subject to stress, spring-rate, and envelope constraints
- **DIN/ASTM compliance** — Wahl shear, Goodman fatigue, and slenderness (buckling) checks with normative justification
- **PostgreSQL** — Relational materials catalogue + full design history with per-iteration snapshots
- **ChromaDB** — Vector store for DIN/ASTM standard clauses, queried during compliance verification
- **Frontend-ready output** — Recharts chart data + Three.js scene parameters in every response

---

## 🗂 Project Structure

```
DB_SpringsAgents/
├── app/
│   ├── main.py                        # FastAPI application entry point
│   ├── agents/
│   │   ├── agent1_requirements.py     # Requirements Analyst (LLM extraction)
│   │   ├── agent2_design.py           # Design Engineer (SciPy optimizer)
│   │   ├── agent3_materials.py        # Materials Engineer (SQL catalogue)
│   │   ├── agent4_compliance.py       # Normative Inspector (DIN/ASTM checks)
│   │   ├── agent5_commercial.py       # Commercial Optimiser (Pandas scoring)
│   │   └── agent6_orchestrator.py     # Orchestrator (conditional routing)
│   ├── api/
│   │   └── v1/
│   │       └── design.py              # POST /api/v1/design
│   ├── core/
│   │   ├── llm_factory.py             # Dynamic multi-provider LLM factory
│   │   └── settings.py                # Pydantic-settings configuration
│   ├── db/
│   │   ├── models.py                  # SQLAlchemy ORM models
│   │   ├── session.py                 # Async engine + session factory
│   │   ├── chromadb_client.py         # ChromaDB ingestion + query helpers
│   │   └── migrations/
│   │       ├── env.py                 # Alembic async migration environment
│   │       └── versions/              # Auto-generated migration files
│   ├── graph/
│   │   └── workflow.py                # LangGraph StateGraph definition
│   ├── schemas/
│   │   └── state.py                   # AgentState + all Pydantic sub-schemas
│   └── tools/
│       └── spring_tools.py            # 4 @tool functions (LangChain)
├── scripts/
│   └── seed_materials.py              # Populate spring_materials table
├── tests/
│   └── test_tools.py                  # Unit tests for all 4 tools
├── .env.example                       # Environment variable template
├── .gitignore
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml                     # Ruff, mypy, pytest configuration
└── requirements.txt
```

---

## 🚀 Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/your-org/DB_SpringsAgents.git
cd DB_SpringsAgents
cp .env.example .env
# Edit .env with your API keys
```

### 2. Start infrastructure services

```bash
docker compose up -d postgres chromadb
```

### 3. Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### 4. Run database migrations and seed

```bash
alembic upgrade head
python -m scripts.seed_materials
```

### 5. Start the API

```bash
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

---

## 🐳 Full Docker Stack

```bash
# Start everything (API + PostgreSQL + ChromaDB)
docker compose up -d

# Also start local Ollama LLM fallback
docker compose --profile local-llm up -d
docker exec springs_ollama ollama pull qwen2.5:3b
```

---

## 📡 API Usage

### Start a design workflow

```bash
curl -X POST http://localhost:8000/api/v1/design/ \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "Design a compression spring to support 120N with 15mm deflection. Max OD 25mm, stainless steel, corrosion resistant.",
    "max_iterations": 5
  }'
```

**Example response (status: approved)**

```json
{
  "session_id": "3f8a1b2c-...",
  "status": "approved",
  "report": {
    "summary": {
      "spring_type": "compression",
      "material": "ASTM A313 Type 302 Stainless Steel",
      "applicable_standard": "DIN 2095 / ASTM A125",
      "approved": true
    },
    "geometry": {
      "wire_diameter_mm": 2.847,
      "mean_coil_diameter_mm": 20.134,
      "outer_diameter_mm": 22.981,
      "active_coils": 9.2,
      "free_length_mm": 58.3,
      "spring_rate_n_mm": 8.003
    },
    "compliance": {
      "safety_factor_shear": 1.82,
      "safety_factor_buckling": 1.45
    },
    "commercial": {
      "ranked_proposals": [
        {
          "proposal_id": "P001",
          "composite_score": 0.7412,
          "material_cost_usd": 0.0023,
          "estimated_life_cycles": 910000,
          "three_js_params": {
            "wireRadius": 1.4235,
            "coilRadius": 10.067,
            "totalCoils": 11.2,
            "height": 58.3
          }
        }
      ]
    }
  }
}
```

### Clarification flow

```bash
# If status == "needs_clarification":
curl -X POST http://localhost:8000/api/v1/design/clarify \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "3f8a1b2c-...",
    "answers": "The spring needs to support 50N. Deflection should be 10mm."
  }'
```

---

## 🧪 Running Tests

```bash
# All tests with coverage
pytest

# Only tool unit tests (no LLM/DB required)
pytest tests/test_tools.py -v

# With coverage report
pytest --cov=app --cov-report=html
```

---

## ⚙️ LLM Configuration

The system reads `LLM_PRIORITY_ORDER` from the environment and tries providers left-to-right:

```env
LLM_PRIORITY_ORDER=gemini,grok,openai,anthropic,ollama
```

On a `RateLimitError` or `QuotaExceededError`, the factory silently rotates to the next provider — no manual intervention needed. Check the current state at:

```bash
GET /api/v1/design/health/llm
```

### Adding a new LLM provider

1. Add a builder function `_build_myprovider()` in [`llm_factory.py`](app/core/llm_factory.py).
2. Register it in `_PROVIDER_BUILDERS`.
3. Add its key to `LLM_PRIORITY_ORDER` in your `.env`.

---

## 🔬 Spring Physics Reference

| Symbol | Meaning | Units |
|--------|---------|-------|
| `d`    | Wire diameter | mm |
| `D`    | Mean coil diameter | mm |
| `C = D/d` | Spring index | — |
| `n_a`  | Active coils | — |
| `k = Gd⁴/(8D³n_a)` | Spring rate | N/mm |
| `τ = 8FD/(πd³)` | Shear stress | MPa |
| `Ks` (Wahl) | `(4C−1)/(4C−4) + 0.615/C` | — |
| `λ = L₀/D` | Slenderness ratio | — |

**Compliance limits enforced:**
- `Ks·τ ≤ 0.45·Sy` (static, DIN 2095)
- `λ ≤ 5.26` (fixed-free buckling, DIN 2095)
- `4 ≤ C ≤ 12` (DIN 2076 / ASTM F1276)
- Goodman criterion `Sf ≥ 1.3` (fatigue, when `cyclic_load=true`)

---

## 📐 Adding a New Spring Type

1. **Agent 2** ([`agent2_design.py`](app/agents/agent2_design.py)) — Add a branch for the new type's parametric equations.
2. **Tool** ([`spring_tools.py`](app/tools/spring_tools.py)) — Add type-specific objective/constraints inside `calculate_spring_geometry_tool`.
3. **Compliance** ([`spring_tools.py`](app/tools/spring_tools.py)) — Add normative check inside `compliance_verification_tool` and corresponding ChromaDB document in [`chromadb_client.py`](app/db/chromadb_client.py).
4. **State** ([`state.py`](app/schemas/state.py)) — Extend `SpringType` enum and add type-specific fields to `SpringGeometry` if needed.

---

## 📦 Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PRIORITY_ORDER` | `gemini,grok,openai,anthropic,ollama` | Provider rotation order |
| `LLM_TEMPERATURE` | `0.1` | LLM sampling temperature |
| `GEMINI_API_KEY` | — | Google AI Studio key |
| `GROK_API_KEY` | — | xAI key |
| `OPENAI_API_KEY` | — | OpenAI key |
| `ANTHROPIC_API_KEY` | — | Anthropic key |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Local model to use |
| `POSTGRES_URL` | `postgresql+asyncpg://...` | Database connection string |
| `CHROMA_HOST` | `localhost` | ChromaDB host |
| `MAX_DESIGN_ITERATIONS` | `5` | Redesign loop cap |

---

## 📄 License

MIT © DB SpringsAgents
