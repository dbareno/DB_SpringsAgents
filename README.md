# Spring Design Agent

**Diseñá resortes mecánicos con lenguaje natural.** Un sistema multi-agente impulsado por LangGraph y FastAPI que interpreta requisitos, optimiza geometría, verifica compliance DIN/ASTM, y genera visualización 3D — todo desde una descripción en castellano o inglés.

---

## Quick Path

```bash
# 1. Clonar y configurar
git clone https://github.com/dbareno/DB_SpringsAgents.git
cd DB_SpringsAgents
cp .env.example .env
# Editar .env con tus API keys (o configurar Ollama local)

# 2. Iniciar infraestructura + app
docker compose up -d

# 3. Abrir http://localhost:3000
# Listo — escribí "Necesito un resorte de compresión para 120N con 15mm de deflexión"
```

---

## Features

| Capability | Detalle |
|-----------|---------|
| **Entrada en lenguaje natural** | "un resorte chico para una birome" o especs técnicas completas |
| **Pipeline de 6 agentes** | Extracción → Materiales → Geometría → Compliance → Scoring → Reporte |
| **Multi-LLM con fallback** | Ollama (local) → Gemini → Grok → OpenAI → Anthropic, rotación automática |
| **Optimización SciPy** | `scipy.optimize.minimize` para volumen mínimo con restricciones de stress/rate/envolvente |
| **Compliance DIN/ASTM** | Wahl shear, Goodman fatiga, pandeo, índice de resorte — con fundamento normativo |
| **Visualización 3D** | Resorte helicoidal interactivo con Three.js (orbit controls, materiales, luces) |
| **Scoring comercial** | Ranking ponderado por costo, seguridad, compacidad |
| **Frontend + API** | Next.js 16 (App Router) + FastAPI, desplegable como Docker o .exe standalone |
| **PostgreSQL + ChromaDB** | Catálogo de materiales + vector store de normas DIN/ASTM |

---

## Arquitectura

```
                        ┌─────────────────────────────────────┐
                        │         LangGraph Pipeline           │
                        │                                      │
User Input ──► Agent 1: Requirements Analyst (LLM extraction)  │
                   │                                           │
        ┌──────────┼───────────┐                               │
        ▼          ▼           ▼                               │
   Clarify      Error    Agent 3: Materials Engineer           │
   (vuelve             (SQL catalogue / ChromaDB)               │
    al user)                   │                                │
                        Agent 2: Design Engineer               │
                        (SciPy optimizer)                      │
                              │                                │
                        Agent 4: Normative Inspector           │
                        (DIN/ASTM compliance)                  │
                              │                                │
                     ┌────────┼────────┐                      │
                     ▼        ▼        ▼                      │
                Redesign   Error   Agent 5: Commercial        │
                (loop)             Optimiser (scoring)         │
                                      │                       │
                                    Report ──► Frontend 3D    │
                        └─────────────────────────────────────┘
```

### Stack técnico

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.11+, FastAPI, LangGraph, LangChain |
| Base de datos | PostgreSQL 16 (asyncpg), SQLAlchemy 2.0 asíncrono |
| Vector store | ChromaDB (normas DIN/ASTM) |
| Optimización | SciPy, NumPy, Pandas |
| LLM local | Ollama (qwen2.5:3b recomendado) |
| Frontend | Next.js 16, Tailwind CSS v4, Three.js, Recharts |
| Contenedores | Docker, Docker Compose |
| Desktop | PyInstaller (.exe standalone) |
| Tests | Pytest, pytest-asyncio, pytest-cov, Ruff, mypy |

---

## Despliegue

### Opción 1: Docker (recomendado)

```bash
# Todo junto
docker compose up -d

# Solo servicios + API (sin frontend)
docker compose up -d postgres chromadb api

# Con Ollama local
docker compose --profile local-llm up -d
docker exec springs_ollama ollama pull qwen2.5:3b
```

**Servicios:**
| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| `frontend` | 3000 | Next.js UI con 3D viewer |
| `api` | 8000 | FastAPI backend + Swagger en /docs |
| `postgres` | 5432 | Base de datos relacional |
| `chromadb` | 8001 | Vector store normativo |
| `ollama` | 11434 | LLM local (perfil: local-llm) |
| `redis` | 6379 | Caching opcional (perfil: redis) |

### Opción 2: Desarrollo local

```bash
# Backend
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt

docker compose up -d postgres chromadb
alembic upgrade head
python -m scripts.seed_materials

uvicorn app.main:app --reload --port 8000

# Frontend (separado)
cd frontend
npm install
npm run dev                 # → http://localhost:3000
```

### Opción 3: .exe standalone

```bash
pip install pyinstaller
python scripts/build_exe.py
# → dist/SpringDesignAgent.exe

# O directamente sin empaquetar:
python scripts/launcher.py
# Abre http://localhost:8000 automáticamente
```

---

## API Usage

### Empezar un diseño

```bash
curl -X POST http://localhost:8000/api/v1/design/ \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "Design a compression spring to support 120N with 15mm deflection. Max OD 25mm, stainless steel, corrosion resistant.",
    "max_iterations": 5
  }'
```

### Responder preguntas del agente

```bash
curl -X POST http://localhost:8000/api/v1/design/clarify \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "3f8a1b2c-...",
    "answers": "The spring needs to support 50N with 10mm deflection. Max OD 15mm."
  }'
```

### Respuesta típica (status: approved)

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
      "spring_rate_n_mm": 8.003
    },
    "compliance": {
      "safety_factor_shear": 1.82,
      "safety_factor_buckling": 1.45
    },
    "commercial": {
      "ranked_proposals": [{...}],
      "chart_data": [{...}]
    },
    "three_js_scene": {
      "spring": {
        "wireRadius": 1.4235,
        "coilRadius": 10.067,
        "totalCoils": 11.2,
        "height": 58.3
      }
    }
  }
}
```

### Health checks

```bash
GET /health                    # System health
GET /api/v1/design/health/llm  # LLM provider status
```

---

## Frontend

La interfaz web (Next.js en `frontend/`) ofrece:

- **Formulario** con textarea para entrada natural + slider de iteraciones
- **Diálogo de clarificación** cuando el agente necesita más datos
- **Visualización 3D** del resorte con Three.js (rotá, hacé zoom)
- **Pestañas:** Geometría (tabla), Compliance (factores de seguridad), Comercial (ranking + gráfico)
- **Historial** de diseños anteriores (localStorage)

**Desarrollo:**
```bash
cd frontend
npm install
npm run dev                  # → localhost:3000
```

**Build estático (para .exe o deploy):**
```bash
cd frontend
npm run build
# → frontend/out/
```

---

## Tests

```bash
# Todos los tests con covertura
pytest

# Tests específicos
pytest tests/test_tools.py -v
pytest tests/test_api.py -v
pytest tests/test_agents.py -v

# Con covertura HTML
pytest --cov=app --cov-report=html
```

**104 tests** — covertura **79%** (agentes ~85%, API ~96%, workflow ~94%).

---

## Configuración LLM

```env
# Orden de proveedores (rotación automática en errores de quota)
LLM_PRIORITY_ORDER=ollama,gemini,grok,openai,anthropic

# Ollama local
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b

# API keys para proveedores remotos
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

El sistema prueba los proveedores en orden. Si uno falla por quota/rate-limit, rota automáticamente al siguiente. Con Ollama local no necesitás ninguna API key.

---

## Project Structure

```
DB_SpringsAgents/
├── app/                          # Backend Python
│   ├── agents/                   # 6 nodos LangGraph
│   │   ├── agent1_requirements.py
│   │   ├── agent2_design.py
│   │   ├── agent3_materials.py
│   │   ├── agent4_compliance.py
│   │   ├── agent5_commercial.py
│   │   └── agent6_orchestrator.py
│   ├── api/v1/design.py          # FastAPI router
│   ├── core/
│   │   ├── llm_factory.py        # Multi-provider LLM
│   │   └── settings.py           # Config (pydantic-settings)
│   ├── db/
│   │   ├── models.py             # SQLAlchemy ORM
│   │   ├── session.py            # Async engine + session
│   │   ├── chromadb_client.py    # Vector store client
│   │   ├── repositories/         # Repository pattern
│   │   └── migrations/           # Alembic
│   ├── graph/workflow.py         # LangGraph StateGraph
│   ├── schemas/                  # Pydantic models
│   ├── services/                 # Business logic
│   ├── tools/spring_tools.py     # 4 @tool functions
│   └── main.py                   # FastAPI entry point
├── frontend/                     # Next.js 16
│   ├── src/
│   │   ├── app/                  # App Router pages
│   │   ├── components/           # UI components (atomic)
│   │   │   ├── ui/               # Atoms (Button, Input, Badge...)
│   │   │   ├── Spring3DViewer    # Three.js spring
│   │   │   ├── DesignForm        # Input form
│   │   │   ├── DesignResult      # Results view
│   │   │   └── ...               # ComplianceCard, ScoreChart, etc.
│   │   └── services/             # POO HTTP clients
│   └── Dockerfile
├── scripts/
│   ├── seed_materials.py         # DB seeder
│   ├── launcher.py               # .exe entry point
│   └── build_exe.py              # PyInstaller build
├── tests/                        # 104 tests
├── docs/                         # Normas DIN/ASTM (PDF)
├── docker-compose.yml            # Full stack
├── Dockerfile                    # Build imagen API
├── launcher.spec                 # PyInstaller spec
├── pyproject.toml                # Ruff, mypy, pytest
└── AGENTS.md                     # Reglas de desarrollo IA
```

---

## Agregar un nuevo tipo de resorte

1. **Agent 2** (`app/agents/agent2_design.py`) — Agregar rama con ecuaciones paramétricas
2. **Tool** (`app/tools/spring_tools.py`) — Agregar constraints en `calculate_spring_geometry_tool`
3. **Compliance** (`app/tools/spring_tools.py`) — Agregar checks en `compliance_verification_tool`
4. **State** (`app/schemas/state.py`) — Extender `SpringType` enum y `SpringGeometry`

---

## Referencia de física de resortes

| Símbolo | Significado | Unidad |
|---------|-------------|--------|
| `d` | Diámetro del alambre | mm |
| `D` | Diámetro medio de espira | mm |
| `C = D/d` | Índice de resorte | — |
| `n_a` | Espiras activas | — |
| `k = Gd⁴/(8D³n_a)` | Constante elástica | N/mm |
| `τ = 8FD/(πd³)` | Tensión de corte | MPa |
| `Ks` (Wahl) | `(4C−1)/(4C−4) + 0.615/C` | — |
| `λ = L₀/D` | Relación de esbeltez | — |

**Límites de compliance:**
- `Ks·τ ≤ 0.45·Sy` (estático, DIN 2095)
- `λ ≤ 5.26` (pandeo, DIN 2095)
- `4 ≤ C ≤ 12` (DIN 2076 / ASTM F1276)
- Goodman `Sf ≥ 1.3` (fatiga, cuando `cyclic_load=true`)

---

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `LLM_PRIORITY_ORDER` | `ollama,gemini,grok,...` | Orden de proveedores |
| `LLM_TEMPERATURE` | `0.1` | Temperatura del LLM |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Modelo local |
| `POSTGRES_URL` | `postgresql+asyncpg://...` | Conexión a PostgreSQL |
| `CHROMA_HOST` | `localhost` | Host de ChromaDB |
| `MAX_DESIGN_ITERATIONS` | `5` | Límite de iteraciones de diseño |
| `DOCS_ENABLED` | `true` | Swagger UI habilitado |

---

## License

MIT © DB SpringsAgents
