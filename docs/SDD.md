# System Design Document — Spring Design Agent

**Status:** Draft for roadmap planning
**Scope:** Target architecture and phased design decisions to evolve the current pipeline into a true agentic quotation system.
**Last updated:** 2026-07-04

---

## 1. Current Architecture Summary

- **Orchestration:** LangGraph `StateGraph` compiled at import in `app/graph/workflow.py`. Nodes: `requirements_analyst` (Agent 1) → conditional (`needs_clarification` | `design_loop` | `error`) → `materials_engineer` (Agent 3) → conditional (`ok` | `error`) → `design_engineer` (Agent 2) → `normative_inspector` (Agent 4) → conditional (`approved` | `redesign` | `iteration_limit` | `error`) → redesign loops back through `increment_iteration` → `materials_engineer`; `commercial_optimiser` (Agent 5) → END. Routing lives in Agent 6 (`app/agents/agent6_orchestrator.py`).
- **State:** `AgentState` dict (`app/schemas/state.py`) with `add_messages` on `messages`, plus `requirements`, `geometry`, `material`, `material_candidates`, `compliance`, `redesign_directives`, `min_yield_strength_mpa`, `commercial_proposals`, `final_report`. Pydantic sub-models: `UserRequirements` (fields such as `spring_rate_n_mm`, `load_force_n`, `max_outer_diameter_mm`, `cyclic_load`), `SpringGeometry` (includes `torsion_moment_n_mm`, `angular_deflection_deg`), `MaterialProperties`, `ComplianceReport` (`retrieved_standards`, `standards_referenced`, `redesign_directives`), `CommercialScore`.
- **Tools:** `app/tools/spring_tools.py` (~1100 lines): `calculate_spring_geometry_tool` (scipy `differential_evolution`), `query_material_properties_tool` (**hardcoded 7-material stub**), `compliance_verification_tool`, `commercial_scoring_tool` (material + winding + heat-treat + shot-peen cost).
- **Persistence:** `app/db/models.py` (`spring_materials`, `design_projects`, `design_iterations`), async SQLAlchemy, Postgres with SQLite fallback. ChromaDB client in `app/db/chromadb_client.py` (currently non-functional at runtime).
- **API:** `app/api/v1/design.py` — `POST /design`, `POST /design/clarify` (re-runs graph from scratch), `GET /design/{id}`, `GET /design/{id}/status`, export PDF/DXF.
- **LLM:** `app/core/llm_factory.py`, Ollama-first with rotation.
- **Packaging:** `scripts/launcher.py` + `launcher.spec` (PyInstaller; bundles `frontend/out`, `app`, Anaconda DLLs; hiddenimports include `chromadb`, `langgraph`, `scipy`).

---

## 2. Target Architecture — Key Design Decisions (mini-ADRs)

### ADR-1 — Multi-turn conversation via LangGraph checkpointer + interrupt

**Context.** `POST /design/clarify` (`app/services/design_service.py:181`) concatenates answers into the raw text and re-runs the entire graph. There is no conversation continuity, no follow-up negotiation, and every clarification pays full pipeline cost. `AgentState` already annotates `messages` with `add_messages`, which is the LangGraph pattern for conversational state, but nothing uses it.

**Decision.**
- Compile the graph with a **checkpointer** (`SqliteSaver`/`AsyncSqliteSaver` for the `.exe`, `AsyncPostgresSaver` for the server), keyed by `thread_id = session_id`.
- Replace the concatenate-and-rerun clarify with a LangGraph **`interrupt`** at the requirements node: when Agent 1 needs input, the graph interrupts and returns questions; the resume call injects the user's answer as a new `HumanMessage` and continues **from the checkpoint**, not from START.
- Add a conversation loop so Agent 1 (and later a dedicated negotiation step) can ask follow-ups and propose trade-offs across turns. The frontend chat drives it: each user message is a resume-with-input on the same `thread_id`.

**Alternatives considered.**
- *Keep re-run, just store history* — cheaper to build but never becomes real negotiation; rejected against Pillar 1.
- *External conversation engine outside LangGraph* — duplicates state; rejected. LangGraph checkpointer is the native mechanism.

**Consequences.** Requires a persistent checkpointer DB (SQLite file in the `.exe`). `AgentState` gains explicit conversation/turn fields. The API changes from fire-and-forget re-run to stateful resume. Local 7B model constraint respected: the LLM only extracts fields and phrases follow-ups/trade-offs (short outputs); routing stays deterministic in Agent 6.

---

### ADR-2 — Materials catalog from the `spring_materials` table with admin CRUD

**Context.** `query_material_properties_tool` (`app/tools/spring_tools.py:422`) is a hardcoded 7-material list despite documenting a SQL query against `spring_materials`. The table exists (`app/db/models.py`) with `name`, `standard`, moduli, strengths, `max_temp_c`, `corrosion_resistant`, `cost_usd_per_kg`, `notes`. Prices cannot be updated without a code change.

**Decision.**
- Make the DB the source of truth. Introduce a `MaterialRepository` and have Agent 3 query it (filter by temperature, corrosion, cost ceiling, min yield; rank; build `material_candidates`). The scoring/ranking logic stays; only the data source moves.
- Seed the table from the current 7 materials via `scripts/seed_materials.py` on first run (idempotent upsert).
- Add **admin CRUD** API (`/api/v1/materials` create/list/update/deactivate) and a frontend admin view; add a **CSV import** endpoint for bulk price/material updates.
- Price flow to Agent 5: because Agent 5 reads `cost_usd_per_kg` from the candidate `MaterialProperties`, sourcing candidates from the DB automatically propagates price updates to quotes — no extra wiring.

**Alternatives considered.**
- *Config file instead of DB* — loses CRUD/audit and duplicates the existing table; rejected.
- *Keep stub, add override file* — half-measure; rejected.

**Consequences.** Tool functions become DB-dependent; unit tests need a seeded in-memory SQLite. Add a soft-delete/`active` flag to `spring_materials` so retiring a material does not break historical `design_iterations` FKs. No packaging impact (SQLAlchemy + SQLite already bundled).

---

### ADR-3 — Fix standards RAG: package-safe embedder + ingestion + citation

**Context.** Agent 4 calls `query_standards` (`app/db/chromadb_client.py`) but ChromaDB's default embedder pulls onnxruntime (DLL load failure locally) and, in the `.exe`, `chromadb.telemetry.product.posthog` is missing from the frozen bundle. It always falls back to hardcoded checks; `retrieved_standards` stays empty.

**Decision.** Evaluate in this order and pick the first that ships cleanly in the `.exe`:
1. **Pin ChromaDB to a version whose telemetry/embedder path is fully importable when frozen**, add the missing `posthog` submodule to `hiddenimports` in `launcher.spec`, and replace the onnxruntime default embedder with a CPU sentence-transformer that PyInstaller can bundle, or a `chromadb`-native embedding that avoids onnxruntime.
2. **If ChromaDB stays fragile in the freeze, switch the vector store to `sqlite-vec` (or SQLite FTS5 for keyword fallback)** with a small local embedding model. This reuses the SQLite runtime already bundled and removes the ChromaDB/onnxruntime/posthog surface entirely.
- Build an **ingestion pipeline** (`scripts/ingest_standards.py`) that chunks DIN/ASTM PDFs and stores clause text with metadata (standard ID, clause number).
- Agent 4 populates `retrieved_standards` (clause text) and `standards_referenced` (standard IDs) and cites them in the report; hardcoded checks remain as the graceful fallback when no relevant clause exists.

**Alternatives considered.**
- *Cloud embeddings* — violates offline constraint; rejected as the primary path (allowed only as an online-mode enhancement).
- *Keep hardcoded-only* — fails Pillar 2 P2-3; rejected.

**Consequences.** The chosen embedder must be tested **inside the frozen `.exe`**, not just locally — this is the historical failure point. Option 2 (`sqlite-vec`/FTS) is the lower-risk offline bet and the recommended default if pinning proves brittle. Ingestion is an offline admin step; shipped standards can be pre-ingested into the bundled store.

---

### ADR-4 — Per-type spring engines via strategy pattern + split the god file

**Context.** `SpringType` enum has compression, extension, torsion, spiral, wave, but `calculate_spring_geometry_tool` and `compliance_verification_tool` are compression-centric. `SpringGeometry` already carries `torsion_moment_n_mm` and `angular_deflection_deg`, so the schema anticipates more types. All logic sits in one ~1100-line `spring_tools.py`.

**Decision.**
- **Enabling refactor first:** split `app/tools/spring_tools.py` into `app/tools/{geometry,materials,compliance,commercial,physics}.py`. `physics.py` holds shared helpers (`_wahl_correction`, `_spring_rate`, `_shear_stress`, `_slenderness_ratio`, the fatigue constants). Tool signatures and behavior are preserved so agents and tests do not change semantics.
- Introduce a **strategy interface** per spring type (`GeometryEngine`, `ComplianceEngine`) selected by `spring_type`. Compression is the reference implementation (already working). Add **extension** (initial tension, hook/end stress) and **torsion** (moment, angular deflection, leg geometry) engines. Spiral/wave remain declared but unimplemented until demanded.
- Add tolerance outputs and type-specific normative checks in the compliance engines.

**Alternatives considered.**
- *One growing function with `if spring_type == ...`* — worsens the god file; rejected.
- *Add types without splitting the file first* — compounds the maintenance debt; rejected. The split is the enabling move.

**Consequences.** The refactor is pure restructuring (Phase 0) and must be covered by the existing test suite (`tests/test_agents.py`, `tests/test_workflow.py`) with no behavior change. New engines are additive and independently shippable. `launcher.spec` `datas` already ships the whole `app` package, so new modules need no packaging change.

---

### ADR-5 — Quotation cost model: lot amortization, margin, price tiers, quote document

**Context.** `commercial_scoring_tool` already computes material + winding + heat-treat + shot-peen cost and a fatigue-aware life estimate (correction to the earlier "material-only" assumption). What is missing to make it a *quotation*: setup amortization by lot size, margin, and lot-based price tiers. Export today is a per-spring technical PDF/DXF, not a priced quote.

**Decision.**
- Extend the cost model to `total_unit_cost(lot_size) = material + manufacturing_ops + setup_cost / lot_size`, then `unit_price = total_unit_cost × (1 + margin)`. Manufacturing ops (winding, grinding, shot peening, finishing) stay; setup amortization and margin are new inputs.
- Compute at least two **lot tiers** (e.g. 100 / 1 000 / 10 000) so larger lots show lower unit price.
- Add a **customer-ready quote document** generator alongside the existing export: ranked options, per-tier unit price, validity date, and cited standards. Reuse `app/services/export_service.py`.
- Persist margin/setup/tier parameters as configurable settings (admin-editable, like materials).

**Alternatives considered.**
- *Hardcode a single lot size and margin* — not a real quote; rejected.
- *External quoting spreadsheet* — breaks the single-tool goal; rejected.

**Consequences.** `CommercialScore` and `final_report` gain tier/price fields. Cost parameters become data (DB or settings), not constants. No new native dependency for the quote PDF (reuse existing export stack).

---

### ADR-6 — LLM strategy under a local 7B model

**Context.** Primary model is local Ollama qwen2.5:7b with cloud fallback (`app/core/llm_factory.py`). A 7B model is reliable for structured extraction, classification, and short justifications, but not long-horizon planning. Agent 1 today mixes regex and LLM extraction.

**Decision.**
- **LLM at judgment points only:** requirement extraction (Agent 1), follow-up/trade-off phrasing (Pillar 1), material-selection rationale, redesign rationale, and per-option explanations. Everything numeric/deterministic (geometry optimization, compliance formulas, scoring, routing) stays in tools and Agent 6.
- **Structured output enforcement:** use Ollama structured-output / JSON-schema (or function-calling) so extraction returns valid `UserRequirements` JSON; keep the regex extractors as a deterministic fallback when the model omits or malforms fields.
- **Fallback & rotation:** keep Ollama-first rotation; add a strict "offline mode" where cloud providers are disabled and the pipeline must complete on the local model alone (the `.exe` default).
- **Evaluation harness:** add a small extraction-quality test set (`tests/`) scoring field-level extraction accuracy against gold requirements, run against the local model to catch regressions.

**Alternatives considered.**
- *Larger local model* — heavier `.exe`, slower; deferred.
- *LLM-driven routing/planning* — unreliable at 7B; rejected in favor of deterministic Agent 6 edges.

**Consequences.** Prompts must target schema-constrained output. The eval harness becomes a gate on prompt/model changes. No packaging change (Ollama runs as an external local service).

---

### ADR-7 — Cross-session memory and won/lost learning

**Context.** `design_projects` / `design_iterations` already log runs, but there is no retrieval by similarity and no feedback loop. Each design starts blind.

**Decision.**
- Add a **design-history search**: index completed designs by requirement features (spring type, force, deflection, envelope, material) and expose a "similar past designs" lookup surfaced at requirement time ("we quoted something similar last month").
- Reuse the vector store chosen in ADR-3 (or a lightweight SQL similarity over normalized requirement fields) so no new dependency is introduced.
- Add a **won/lost outcome field** on `design_projects` and use aggregate outcomes as a soft signal in ranking rationale (not as a hard override).

**Alternatives considered.**
- *Separate memory service* — over-engineered for a local `.exe`; rejected.
- *Full ML learning-to-rank* — premature; a similarity lookup + outcome tagging is enough for the first slice.

**Consequences.** New nullable columns on `design_projects` (outcome, embedding/feature key). Memory must work offline in the `.exe`. Privacy is local-only (no cloud sync), consistent with the offline constraint.

---

## 3. Data Model Changes (summary)

- `spring_materials`: add `active` (soft-delete) flag; keep `standard`, `notes`. Becomes the runtime source of truth (ADR-2).
- New settings/config for cost parameters: setup cost, margin, lot tiers (ADR-5) — DB table or settings store.
- `design_projects`: add `outcome` (won/lost/pending) and a requirement feature/embedding key (ADR-7); optionally `quote_snapshot` (JSON) for the exported quote.
- `CommercialScore` / `final_report`: add per-lot-tier unit price, margin, setup amortization, and per-option rationale fields.
- Checkpointer store: LangGraph checkpoint tables (SQLite file for `.exe`, Postgres for server) keyed by `session_id` (ADR-1).
- Standards store: ChromaDB collection **or** `sqlite-vec`/FTS table with clause text + metadata (ADR-3).

---

## 4. API Surface Changes (summary)

- `POST /design/clarify` → replaced/augmented by a **stateful resume** endpoint that continues the checkpointed thread with a new user message (ADR-1); the chat frontend calls it per turn.
- New `/api/v1/materials` CRUD + CSV import (ADR-2).
- New `/api/v1/standards/ingest` (admin) for PDF ingestion (ADR-3).
- New quote export endpoint / extension of existing export for the customer-ready quotation with lot tiers (ADR-5).
- New `/api/v1/designs/search` for similar past designs and an outcome-tagging endpoint (ADR-7).
- Existing `POST /design`, `GET /design/{id}`, `GET /design/{id}/status`, PDF/DXF export are preserved.

---

## 5. Packaging Impact on the PyInstaller `.exe` (per decision)

| Decision | Packaging impact |
|----------|------------------|
| ADR-1 Checkpointer | SQLite saver uses the already-bundled `aiosqlite`/`sqlite3`; add a writable checkpoint DB path under the exe's data dir. Low risk. |
| ADR-2 Materials DB | None new (SQLAlchemy + SQLite already bundled). Seed script must run on first launch. |
| ADR-3 Standards RAG | **Highest packaging risk.** Must validate the embedder *inside the frozen exe*. If ChromaDB, add missing hidden submodules (e.g. `chromadb.telemetry.product.posthog`) and a bundleable embedder; if `sqlite-vec`/FTS, remove ChromaDB from `hiddenimports` and bundle the local embedding model. Pre-ingested standards store shipped as `datas`. |
| ADR-4 Engines + split | None; `launcher.spec` `datas` already ships the whole `app/` package. |
| ADR-5 Quote model | None new; reuse existing export stack. |
| ADR-6 LLM strategy | None; Ollama is external. Ensure offline mode disables cloud cleanly. |
| ADR-7 Memory | Reuse ADR-3 store or SQL; no new native dependency. |

---

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| RAG embedder still fails when frozen | Pillar 2 blocked again | Prefer `sqlite-vec`/FTS (ADR-3 option 2); gate on a smoke test that runs retrieval inside the built `.exe`, not just locally |
| Checkpointer write path in `.exe` (read-only install dir) | Conversation persistence breaks | Store checkpoint/materials/standards DBs in a per-user writable data dir, not next to the exe |
| God-file split introduces regressions | Everything downstream breaks | Do the split as pure refactor in Phase 0, behavior-locked by existing `tests/test_agents.py` and `tests/test_workflow.py` |
| 7B model malforms structured output | Bad extraction, bad quotes | JSON-schema/function-calling enforcement + regex fallback + extraction eval harness (ADR-6) |
| Extension/torsion physics errors | Wrong, unsafe designs | Validate new engines against textbook worked examples before enabling in the UI |
| Cost-model changes silently alter existing quotes | Commercial trust | Make cost parameters explicit data with defaults matching current behavior; snapshot quote parameters per design |
| Historical FK breakage when retiring materials | Data integrity | Soft-delete via `active` flag, never hard-delete referenced materials |

---

## 7. Phase Mapping

Phase cut chosen: **Phase 0 = enabling refactors + fixes that unblock everything**, then ordered by user value against dependency. Each phase is independently shippable.

- **Phase 0 — Enabling refactors & fixes (foundation).**
  - ADR-4 (split `spring_tools.py` into `geometry/materials/compliance/commercial/physics`, behavior-locked). Refactor only, no new features.
  - Repo hygiene: stop e2e artifacts polluting the repo root.
  - Prep the writable data-dir for the `.exe` (needed by Phases 1–3 DBs).

- **Phase 1 — Real data foundation.**
  - ADR-2 (materials DB source of truth + seed + admin CRUD + CSV import). Highest trust payoff, unblocks real quotes.

- **Phase 2 — Standards RAG working.**
  - ADR-3 (package-safe embedder, ingestion, Agent 4 citations). Depends on Phase 0 data-dir; validated inside the `.exe`.

- **Phase 3 — Conversation.**
  - ADR-1 (checkpointer + interrupt-based multi-turn, trade-off dialogue, chat-driven frontend). Depends on the Phase 0 data-dir/checkpointer store.

- **Phase 4 — Engineering depth.**
  - ADR-4 continued: extension then torsion engines, tolerances, type-specific compliance. Depends on the Phase 0 strategy split.

- **Phase 5 — Quotation & commercial.**
  - ADR-5 (lot amortization, margin, tiers, quote document). Depends on Phase 1 (real prices) to be meaningful.

- **Phase 6 — Autonomy & memory.**
  - ADR-6 (structured output, offline mode, eval harness) and ADR-7 (design-history search, won/lost learning). ADR-6 partially underpins Phase 3 quality and can start in parallel; ADR-7 depends on Phase 2's store.

Ordering rationale: refactor before feature work (Phase 0); real prices before real quotes (Phase 1 before Phase 5); the offline-risky RAG (Phase 2) validated early so its packaging failure mode is discovered before it blocks later phases; conversation (Phase 3) is high user value but depends only on the foundation; engineering depth (Phase 4) is additive on the strategy split; quotation (Phase 5) is where the four pillars converge into the deliverable; autonomy/memory (Phase 6) is the polish that makes it feel "agentic."
