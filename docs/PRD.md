# Product Requirements Document — Spring Design Agent

**Status:** Draft for roadmap planning
**Owner:** Engineering (product direction confirmed with product owner)
**Last updated:** 2026-07-04

---

## 1. Vision

Spring Design Agent is an **agentic engineer-in-the-loop system** for an engineering team that produces **customer-ready commercial proposals and quotations** for mechanical springs. An engineer describes a requirement in natural language; the system negotiates the missing details like a colleague would, runs real spring engineering (geometry optimization, normative compliance, fatigue), and returns a ranked set of manufacturable options priced as a quotation the team can send to a customer with minimal rework. The system is local-first: it must run as an offline Windows standalone executable using a local LLM, with cloud models as fallback only.

---

## 2. Problem & Current State

The current system (FastAPI backend in `app/`, Next.js static-export frontend in `frontend/`, distributed both as a server and as a ~106 MB PyInstaller `.exe`) already delivers an end-to-end pipeline via LangGraph (`app/graph/workflow.py`) with six agents. It is a strong skeleton but is not yet a "true agentic" quotation tool. Honest inventory:

### What works today (keepers — do not rewrite)
- **LangGraph topology** (`app/graph/workflow.py`): requirements → materials → design → compliance → (redesign loop, max 5) → commercial → END, with conditional routing after requirements, materials, and compliance. This orchestration is sound.
- **Geometry optimizer** (`calculate_spring_geometry_tool` in `app/tools/spring_tools.py`): scipy `differential_evolution` solving for wire diameter, mean coil diameter, and active coils, with a Goodman fatigue constraint for cyclic loads. Solid for **compression** springs.
- **Multi-option materials + commercial ranking**: Agent 3 builds a top-N `material_candidates` shortlist (`app/schemas/state.py`); Agent 5 evaluates each candidate deterministically and ranks them via `commercial_scoring_tool`, producing a `final_report` with ranked options.
- **Cost model with manufacturing operations** (correction to prior assumptions): `commercial_scoring_tool` already computes material cost **plus** winding cost (index-dependent), heat treatment (stress relief + quench/temper above 1500 MPa), and shot peening for cyclic loads. It also estimates fatigue life. What it still lacks: **setup amortization by lot size, margin, and lot-based price tiers** — i.e. it prices a *spring*, not a *quotation*.
- **PDF/DXF export** (`app/services/export_service.py`, endpoints in `app/api/v1/design.py`).
- **LLM factory** (`app/core/llm_factory.py`): Ollama-first with quota-triggered rotation to cloud providers.
- **DB layer** (`app/db/models.py`): `spring_materials`, `design_projects`, `design_iterations` tables with SQLAlchemy async; PostgreSQL with SQLite fallback.
- **Standalone `.exe` launcher** (`scripts/launcher.py`, `launcher.spec`): serves the SPA and opens the browser, offline-capable.

### What is stub, broken, or shallow
- **Materials catalog is a hardcoded stub.** `query_material_properties_tool` (`app/tools/spring_tools.py:422`) documents itself as "In production this executes a parameterised SQL query against the `spring_materials` table" but actually returns an in-code list of **7 hardcoded materials**. The `SpringMaterial` table exists and is unused as the runtime source of truth. Prices cannot be updated without editing code.
- **Standards RAG is broken.** Agent 4 (`app/agents/agent4_compliance.py`) calls `query_standards` from `app/db/chromadb_client.py`, but ChromaDB fails to load (locally: onnxruntime DLL failure; in the `.exe`: `No module named 'chromadb.telemetry.product.posthog'`). It always falls back to hardcoded formulas. `retrieved_standards` / `standards_referenced` are effectively empty in practice.
- **Clarification is single-shot, not a conversation.** When Agent 1 marks requirements incomplete, the API returns questions; `POST /api/v1/design/clarify` (`app/services/design_service.py:181`) concatenates the answers into the original text and **re-runs the whole graph from scratch**. There is no conversation state, no follow-up negotiation, no trade-off dialogue.
- **Compression-only depth.** `SpringType` enum includes extension, torsion, spiral, wave, but the geometry/compliance engines are compression-centric; other types are weakly or not supported.
- **Naive-quotation gap.** No lot-size pricing, no margin, no setup amortization; the "quote" is a per-spring cost, not a customer document.
- **God file.** `app/tools/spring_tools.py` is ~1100 lines mixing geometry, materials, compliance, commercial, and physics.
- **No memory across designs.** Each run is independent; there is no "we quoted something similar last month."
- **No auth / multi-user.** Session IDs exist but no identity or access control.

---

## 3. Users & Jobs-to-be-Done

**Primary user: the proposal/quotation engineering team.** They receive a customer inquiry, need to turn it into a defensible engineering design and a priced, standards-backed quotation, and send it to the customer.

| Job | Today | Desired |
|-----|-------|---------|
| Turn a vague inquiry into a complete spec | One-shot Q&A round, then re-run | Multi-turn negotiation with follow-ups and trade-off proposals |
| Trust the material data and prices | Edit code to change a price | Edit the catalog in a UI; prices flow straight into quotes |
| Cite the governing standard to the customer | Hardcoded standard name only | Retrieved, quotable normative clauses in the report |
| Quote extension/torsion springs, not just compression | Compression only | Real support for extension and torsion |
| Produce a sendable quotation | Per-spring cost + PDF drawing | Customer-ready quote: options, lot tiers, validity, margin |
| Reuse prior work | Nothing | Search past designs; learn from won/lost quotes |

---

## 4. Product Requirements (grouped by the four agentic pillars)

The four pillars are **equally weighted**: (1) real multi-turn conversation, (2) real data not stubs, (3) engineering depth, (4) pipeline autonomy.

### Pillar 1 — Real multi-turn conversation

**P1-1 — Conversational requirement negotiation**
Statement: As an engineer, I want the system to ask follow-up questions and refine the design across multiple turns, so I can arrive at a complete spec through dialogue rather than one form.
Acceptance:
- The system can ask a follow-up, receive an answer, and ask a further follow-up in the same session without re-running the pipeline from scratch.
- Conversation state persists across turns (survives process restart within the session window).
- Median clarification exchanges to a complete spec is tracked and reported.

**P1-2 — Trade-off dialogue**
Statement: As an engineer, I want the system to propose trade-offs (e.g. "a smaller OD forces a higher stress; accept a lower safety factor or a stronger alloy?") and let me choose.
Acceptance:
- When a constraint conflict is detected, the system presents at least one explicit trade-off with the affected quantities.
- The user's choice is carried forward into the design without re-entering prior answers.

**P1-3 — Iterative refinement of an existing result**
Statement: As an engineer, I want to say "make it cheaper" or "use stainless" on an existing result and get an updated proposal.
Acceptance:
- A follow-up instruction on a completed design produces a revised ranked proposal referencing the previous one.

### Pillar 2 — Real data, not stubs

**P2-1 — DB-backed materials catalog as source of truth**
Statement: As a catalog admin, I want the materials used by the engine to come from the `spring_materials` table, not hardcoded code.
Acceptance:
- `query_material_properties_tool` reads from the database; removing the hardcoded list does not break the pipeline.
- Seed/import populates the table (including at least the current 7 materials) on first run.

**P2-2 — Admin CRUD for materials and prices**
Statement: As a catalog admin, I want to add, edit, and retire materials and update prices from a UI.
Acceptance:
- Create/read/update/deactivate a material via API and UI.
- A price change is reflected in the next quote without a code change or restart.
- CSV import path exists for bulk updates.

**P2-3 — Working standards RAG**
Statement: As an engineer, I want the compliance report to cite real DIN/ASTM clauses retrieved from ingested standards.
Acceptance:
- Retrieval works both locally and inside the packaged `.exe` (no onnxruntime/posthog failure).
- Ingestion pipeline accepts DIN/ASTM PDFs.
- Agent 4 populates `retrieved_standards` and `standards_referenced` with actual retrieved clause text when relevant standards exist; falls back gracefully when they do not.

### Pillar 3 — Engineering depth

**P3-1 — Extension spring support**
Statement: As an engineer, I want to design extension springs including hook/end stress and initial tension.
Acceptance:
- Geometry + compliance produce a valid extension design for a representative case, including initial tension and hook-region stress checks.

**P3-2 — Torsion spring support**
Statement: As an engineer, I want to design torsion springs (moment, angular deflection, leg geometry).
Acceptance:
- Geometry + compliance produce a valid torsion design using moment/angle inputs (`torsion_moment_n_mm`, `angular_deflection_deg` already exist in `SpringGeometry`).

**P3-3 — Tolerances and expanded normative checks**
Statement: As an engineer, I want tolerance outputs and more normative checks so the quote is manufacturable and defensible.
Acceptance:
- Output includes dimensional tolerances per applicable standard.
- Compliance covers at least the current checks plus type-specific checks for extension/torsion.

### Pillar 4 — Pipeline autonomy

**P4-1 — LLM-reasoned redesign**
Statement: As an engineer, I want redesign iterations to reason about *why* a design failed and adjust intelligently, not just replay fixed directives.
Acceptance:
- Redesign directives include an LLM-generated rationale grounded in the failure modes, within what a local 7B model can reliably produce (short structured justifications).

**P4-2 — Trade-off explanations in the report**
Statement: As an engineer, I want each ranked option to carry a short human explanation of why it ranks where it does.
Acceptance:
- Each ranked option in `final_report` includes a concise rationale (cost vs durability vs compactness).

**P4-3 — Cross-session memory**
Statement: As an engineer, I want to find prior similar designs and reuse them.
Acceptance:
- Past designs are searchable by requirement similarity; a match surfaces in a new session.

### Cross-pillar — Quotation output

**P5-1 — Customer-ready quotation document**
Statement: As an engineer, I want to export a quotation with options, lot-size price tiers, margin, and validity, not just a per-spring cost.
Acceptance:
- Cost model includes setup amortization by lot size, margin, and at least two lot tiers.
- Exported quote document lists ranked options with per-tier unit price and a validity date.

---

## 5. Success Metrics

- **Quote-ready without rework:** % of designs whose exported quotation is sent to a customer with no manual engineering rework. Target: rising trend, ≥70% at maturity.
- **Clarification exchanges to completion:** median follow-up turns to reach a complete spec. Target: ≤3.
- **Quote turnaround time:** median wall-clock from first inquiry message to exported quotation.
- **Catalog coverage:** number of active materials in `spring_materials`; % of quotes using DB-sourced (non-stub) prices. Target: 100% DB-sourced.
- **Standards citation rate:** % of compliance reports that cite at least one retrieved clause when a relevant standard is ingested.
- **Spring-type coverage:** % of inquiries served by a real engine (compression + extension + torsion) vs unsupported.
- **Offline success rate:** % of full runs completing in the `.exe` with no internet using the local LLM only.

---

## 6. Non-Goals (explicit)

- **No CAD/CAM integration** beyond the existing DXF export in this roadmap.
- **No ERP/MRP integration** (order management, inventory, invoicing).
- **No cloud-only features** that would break the offline `.exe`. Any capability must degrade gracefully or run locally.
- **No full CRM / customer-account management.**
- **No rewrite** of the LangGraph topology, the `.exe` launcher, or the multi-option materials/commercial feature — these are keepers and are extended, not replaced.
- **No real-time collaborative editing / multi-user concurrency** in this roadmap (single-engineer sessions; auth is out of scope unless later prioritized).

---

## 7. Constraints

- **Local-first LLM.** Primary model is a local Ollama ~7B-class model (qwen2.5:7b). Cloud providers are fallback only. Design decisions must respect what a 7B model does reliably (structured extraction, classification, short justifications) vs unreliably (long-horizon planning). Push determinism into tools; use the LLM at judgment points.
- **Offline standalone `.exe`.** The PyInstaller build (`launcher.spec`) must keep working offline. Every dependency choice (especially the standards RAG embedder) must be packageable and must not require network access at runtime.
- **Windows distribution.** Primary target is the Windows `.exe`; build tooling and native dependencies (DLLs bundled from Anaconda `Library/bin`) are Windows-oriented.
- **Existing 6-agent core preserved.** The agent responsibilities and graph edges are extended, not restructured.
- **Database duality.** PostgreSQL in server mode, SQLite fallback for the `.exe`; schema and features must work on both.
