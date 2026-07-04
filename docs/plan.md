# Spring Design Agent — Phased Execution Plan

Source documents: [PRD.md](./PRD.md) · [SDD.md](./SDD.md)

## How to use this plan

Each phase is **independently shippable** — merge and deploy Phase N without needing Phase N+1 to exist. Within a phase, work the task checklist top to bottom; tasks are ordered by dependency. Cut sequential PRs within a phase and keep every PR reviewable at roughly **≤400 changed lines**; if a task grows past that, split it into two PRs along a natural seam (e.g. schema change / logic change, or backend / frontend). Check off tasks as they land. Do not skip ahead to a later phase's tasks even if they look easy — dependencies are real (see the table below).

---

## Phase Overview

| Phase | Goal | PRD requirements covered | ADRs implemented | Size | Dependencies |
|-------|------|---------------------------|-------------------|------|---------------|
| 0 | Enabling refactors & fixes (foundation) | Non-functional (unblocks all pillars) | ADR-4 (split only) | M | none |
| 1 | Real data foundation — materials DB as source of truth | P2-1, P2-2 | ADR-2 | M | Phase 0 |
| 2 | Standards RAG working, offline-safe | P2-3 | ADR-3 | L | Phase 0 |
| 3 | Multi-turn conversation & trade-off dialogue | P1-1, P1-2, P1-3 | ADR-1 | L | Phase 0 |
| 4 | Engineering depth — extension & torsion | P3-1, P3-2, P3-3 | ADR-4 (continued) | L | Phase 0 |
| 5 | Quotation & commercial model | P5-1 | ADR-5 | M | Phase 1 |
| 6 | Autonomy & memory | P4-1, P4-2, P4-3 | ADR-6, ADR-7 | M | Phase 2, Phase 3 (partial) |

Total tasks: **Phase 0 — 7 · Phase 1 — 9 · Phase 2 — 10 · Phase 3 — 11 · Phase 4 — 10 · Phase 5 — 8 · Phase 6 — 9** (64 tasks).

---

## Definition of Done (every phase)

- [ ] `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_standards_ingestion.py` is green (the ignored file has a pre-existing environmental onnxruntime DLL failure unrelated to this work).
- [ ] `npx tsc --noEmit` is clean in `frontend/`.
- [ ] `npm run build` succeeds in `frontend/`.
- [ ] For any phase flagged exe-affecting below: `.venv/Scripts/python.exe scripts/build_exe.py` completes and the resulting `.exe` passes a manual smoke run (launch, load the SPA, submit one design request, confirm no console/DLL errors).
- [ ] `docs/PRD.md` / `docs/SDD.md` updated if the phase's work changes a decision recorded there (e.g. ADR-3 embedder choice finalized).
- [ ] No new files added to the repo root (e2e artifacts, scratch scripts, etc. go under `scripts/`, `tests/`, or are removed before merge — this is itself a Phase 0 fix, see below).
- [ ] Each PR in the phase stays close to ~400 changed lines; split further if it doesn't.

---

## Phase 0 — Enabling Refactors & Fixes (Foundation)

**Goal.** Split the god file into per-concern modules with zero behavior change, and prepare the writable data directory that every later DB-backed phase needs. Pure refactor — no new features, no new tests beyond regression locks.

**Deliverables**
- `app/tools/spring_tools.py` decomposed into `app/tools/geometry.py`, `app/tools/materials.py`, `app/tools/compliance.py`, `app/tools/commercial.py`, `app/tools/physics.py`.
- A writable per-user data directory resolver usable by both server and `.exe` modes.
- Clean repo root (no stray e2e artifacts).

**Task checklist**
- [ ] Extract shared numeric helpers (`_wahl_correction`, `_spring_rate`, `_shear_stress`, `_slenderness_ratio`, fatigue constants) from `app/tools/spring_tools.py` into `app/tools/physics.py`.
- [ ] Move `calculate_spring_geometry_tool` into `app/tools/geometry.py`, importing shared helpers from `app/tools/physics.py`. Preserve function signature exactly.
- [ ] Move `query_material_properties_tool` into `app/tools/materials.py` (still the hardcoded stub at this point — DB migration is Phase 1). Preserve signature.
- [ ] Move `compliance_verification_tool` into `app/tools/compliance.py`. Preserve signature.
- [ ] Move `commercial_scoring_tool` into `app/tools/commercial.py`. Preserve signature.
- [ ] Update all imports across `app/agents/agent2_design.py`, `app/agents/agent3_materials.py`, `app/agents/agent4_compliance.py`, `app/agents/agent5_commercial.py`, and `tests/test_tools.py` to the new module paths; delete `app/tools/spring_tools.py` once all call sites are migrated.
- [ ] Add a data-dir resolver (e.g. `app/core/paths.py`) that returns a writable per-user directory for server mode (project-local `./data`) and `.exe` mode (`%LOCALAPPDATA%/SpringDesignAgent` or equivalent), used later by the checkpointer (Phase 3), materials DB (Phase 1), and standards store (Phase 2).

**Acceptance criteria**
- No PRD P-ID directly maps to Phase 0 (it is non-functional), but it is the prerequisite for P2-1, P2-3, P1-1..P1-3, P3-1..P3-3 per the SDD's stated dependency chain.
- `tests/test_tools.py` and `tests/test_agents.py` pass unmodified in assertions (only import paths change) — proves zero behavior change.
- Repo root has no e2e/build artifacts left after a full test + build cycle.

**Test strategy**
- Existing `tests/test_agents.py` and `tests/test_workflow.py` are the behavior lock — SDD explicitly calls these out as the regression gate for this refactor (ADR-4 consequences). No new test logic is needed; only import updates.
- Add one new unit test for the data-dir resolver covering server-mode and frozen-exe-mode branching (mock `sys.frozen`).
- **Exe smoke test required**: SDD packaging-impact table does not flag ADR-4 itself as exe-risk, but this phase changes `app/tools/` module layout that `launcher.spec` bundles wholesale — verify `scripts/build_exe.py` still completes and the exe launches cleanly.

**Rollback/risk note**
- SDD risk table: "God-file split introduces regressions → everything downstream breaks." Mitigation already applied above: refactor is behavior-locked by the existing test suite before any other phase starts. If a regression surfaces post-merge, revert the module split PR; no data migrations are involved so rollback is a pure code revert.

---

## Phase 1 — Real Data Foundation (Materials DB as Source of Truth)

**Goal.** Make `spring_materials` the runtime source of truth for material data and give catalog admins CRUD + CSV import, so prices update without a code change.

**Deliverables**
- `query_material_properties_tool` reads from the DB via `MaterialRepository`.
- `active` soft-delete flag on `SpringMaterial`.
- Admin CRUD API (`/api/v1/materials`) + CSV import endpoint.
- Idempotent seed script populating at least the current 7 materials.
- Frontend admin view for materials.

**Task checklist**
- [ ] Add `active: Mapped[bool]` (default `True`) column to `SpringMaterial` in `app/db/models.py`; add an Alembic migration under `app/db/migrations/versions/`.
- [ ] Extend `app/db/repositories/material_repository.py` (`MaterialRepository`) with `create`, `update`, `deactivate`, and filtered `list` (by temperature, corrosion resistance, cost ceiling, min yield strength) methods; `get_all` should default to `active=True` only.
- [ ] Rewrite `scripts/seed_materials.py` to upsert the current 7 hardcoded materials into `spring_materials` idempotently (safe to re-run); run it from the `.exe` first-launch path (`scripts/launcher.py`) and from server startup (`app/main.py`).
- [ ] Replace the hardcoded list in `app/tools/materials.py`'s `query_material_properties_tool` with a call through `MaterialRepository`, keeping the tool's existing return shape so `app/agents/agent3_materials.py` needs no changes to its consumption logic.
- [ ] Add `app/schemas/design.py` (or a new `app/schemas/material.py`) Pydantic request/response models for material create/update/CSV row.
- [ ] Add `/api/v1/materials` CRUD endpoints (`app/api/v1/materials.py`, new router registered in `app/main.py`): `POST` create, `GET` list, `PATCH` update, `DELETE`/`POST /deactivate` soft-delete.
- [ ] Add `POST /api/v1/materials/import` CSV bulk-import endpoint reusing the create/update path per row; return a per-row success/error summary.
- [ ] Add a frontend admin view (new `frontend/src/app/admin/materials/page.tsx` or extend `frontend/src/components/MaterialOptions.tsx` context) with a table + create/edit form + CSV upload, wired through `frontend/src/services/design-service.ts` (or a new `materials-service.ts`) and `frontend/src/services/types.ts`.
- [ ] Update `tests/test_repositories.py` and `tests/test_tools.py` to seed an in-memory SQLite DB with test materials instead of relying on the hardcoded stub; add API tests to `tests/test_api.py` for the CRUD + CSV import endpoints.

**Acceptance criteria (P2-1, P2-2)**
- `query_material_properties_tool` reads from the database; removing the hardcoded list does not break the pipeline (P2-1).
- Seed populates the table with at least the current 7 materials on first run, idempotently (P2-1).
- Create/read/update/deactivate a material works via API and UI (P2-2).
- A price change is reflected in the next quote without a code change or restart (P2-2).
- CSV import path exists for bulk updates (P2-2).

**Test strategy**
- New: `tests/test_repositories.py` cases for `MaterialRepository` CRUD + filtered list + soft-delete-excludes-from-list.
- New: `tests/test_api.py` cases for `/api/v1/materials` CRUD and `/import` (valid CSV, malformed row).
- Regression: `tests/test_agents.py` (Agent 3 candidate-building) must still pass against DB-sourced data.
- Regression gate: full `pytest tests/ -q --ignore=tests/test_standards_ingestion.py`.
- **Exe smoke test required** — SDD packaging table: "None new (SQLAlchemy + SQLite already bundled). Seed script must run on first launch." Verify the seed actually runs and materials are queryable inside a built `.exe`.

**Rollback/risk note**
- SDD risk: "Historical FK breakage when retiring materials → data integrity." Mitigation: soft-delete via `active` flag only, never hard-delete a material referenced by `design_iterations.material_id`. If the DB-sourced path misbehaves in production, the `MaterialRepository` change is isolated to `app/tools/materials.py`'s call site — revertible independently of the CRUD API.

---

## Phase 2 — Standards RAG Working (Offline-Safe)

**Goal.** Fix standards retrieval so Agent 4 cites real DIN/ASTM clauses, in a way that survives being frozen into the `.exe` — the historical failure point (onnxruntime DLL locally, missing `posthog` submodule when frozen).

**Deliverables**
- A package-safe retrieval store validated inside the frozen `.exe`, not just locally.
- `scripts/ingest_standards.py` ingestion pipeline for DIN/ASTM PDFs.
- Agent 4 populating `retrieved_standards` / `standards_referenced` with real clause text, falling back gracefully.

**Task checklist**
- [ ] Spike and decide the embedder path per ADR-3 order: (a) pin ChromaDB + bundleable CPU embedder + add `chromadb.telemetry.product.posthog` to `hiddenimports` in `launcher.spec`, tested inside a built exe; if that proves brittle, (b) switch to `sqlite-vec` (or SQLite FTS5 keyword fallback) with a small local embedding model. Record the decision and rationale back into `docs/SDD.md` ADR-3.
- [ ] If option (b): remove `chromadb` usage from `app/db/chromadb_client.py` (or replace its internals) and add the new store client, keeping the existing `query_standards` function signature so `app/agents/agent4_compliance.py` doesn't need interface changes.
- [ ] If option (a): update `launcher.spec` `hiddenimports` with the missing ChromaDB submodules and confirm the chosen embedder has no onnxruntime dependency, or bundle a working onnxruntime DLL set.
- [ ] Build/extend `scripts/ingest_standards.py`: chunk DIN/ASTM PDFs, extract clause number + text, write to the chosen store with metadata (`standard_id`, `clause_number`, `source_file`).
- [ ] Add `/api/v1/standards/ingest` admin endpoint (new `app/api/v1/standards.py`, router registered in `app/main.py`) that accepts a PDF upload and runs the ingestion pipeline synchronously or via a background task.
- [ ] Update `app/agents/agent4_compliance.py` to call `query_standards`, populate `retrieved_standards` (clause text) and `standards_referenced` (standard IDs) in `ComplianceReport` when relevant clauses are found, and keep the existing hardcoded-formula path as fallback when retrieval returns nothing.
- [ ] Pre-ingest at least one representative DIN/ASTM standard into the shipped store so `launcher.spec` `datas` includes a working starter dataset for the `.exe`.
- [ ] Store the standards store path under the Phase 0 data-dir resolver (`app/core/paths.py`) so it is writable in both server and `.exe` modes.
- [ ] Update `tests/test_standards_ingestion.py` to run against the chosen store (not ChromaDB with onnxruntime) — note this may finally unblock the currently-ignored test; if the onnxruntime dependency is fully removed (option b), un-ignore it in the pytest command going forward.
- [ ] Add unit tests for Agent 4's citation logic (`tests/test_agents.py`): retrieval hit → clause text populated; retrieval miss → graceful fallback to hardcoded checks.

**Acceptance criteria (P2-3)**
- Retrieval works both locally and inside the packaged `.exe` — no onnxruntime/posthog failure (P2-3).
- Ingestion pipeline accepts DIN/ASTM PDFs (P2-3).
- Agent 4 populates `retrieved_standards` and `standards_referenced` with actual retrieved clause text when relevant standards exist; falls back gracefully when they do not (P2-3).

**Test strategy**
- New/rehabilitated: `tests/test_standards_ingestion.py` against the finalized store.
- New: Agent 4 citation unit tests (hit/miss paths) in `tests/test_agents.py`.
- Regression: `tests/test_workflow.py` (Agent 4 node still routes correctly with populated or empty `retrieved_standards`).
- **Exe smoke test mandatory and highest-priority** — SDD flags this as "Highest packaging risk" and the risk table's #1 entry ("RAG embedder still fails when frozen → Pillar 2 blocked again"). Do not consider this phase done until retrieval is verified to actually run inside `scripts/build_exe.py`'s output, not just under `pytest`.

**Rollback/risk note**
- SDD risk: prefer `sqlite-vec`/FTS as the lower-risk default if ChromaDB pinning proves brittle; gate on an in-exe smoke test before enabling in the UI. If retrieval breaks post-release, Agent 4's fallback-to-hardcoded-checks path means compliance reports degrade gracefully rather than failing the pipeline — this fallback must never be removed.

---

## Phase 3 — Multi-Turn Conversation & Trade-off Dialogue

**Goal.** Replace the concatenate-and-rerun clarification flow with a LangGraph checkpointer + `interrupt` pattern so engineers can negotiate requirements and trade-offs across turns without replaying the whole pipeline.

**Deliverables**
- Graph compiled with a checkpointer (`SqliteSaver`/`AsyncSqliteSaver` for `.exe`, `AsyncPostgresSaver` for server), keyed by `thread_id = session_id`.
- `interrupt`-based clarification at the requirements node, replacing the rerun-from-scratch flow.
- Trade-off proposal surfaced when a constraint conflict is detected.
- Chat-driven frontend resume flow.

**Task checklist**
- [ ] Add checkpointer selection to `app/graph/workflow.py`: `AsyncSqliteSaver` pointed at the Phase 0 data-dir path when running as `.exe`/local, `AsyncPostgresSaver` when a Postgres DSN is configured for server mode.
- [ ] Extend `AgentState` (`app/schemas/state.py`) with explicit conversation/turn tracking fields needed alongside the existing `add_messages`-annotated `messages` (e.g. `turn_count`, `pending_question`, `trade_off_options`).
- [ ] Replace the concatenate-and-rerun logic in `app/services/design_service.py` (`POST /api/v1/design/clarify` handler, currently around line 181) with a stateful resume: inject the user's answer as a new `HumanMessage` and continue the graph from the checkpoint via `thread_id = session_id`, not from `START`.
- [ ] Add a LangGraph `interrupt` call in `app/agents/agent1_requirements.py` when requirements are incomplete, replacing the current "return questions and stop" behavior with a proper graph interrupt/resume boundary.
- [ ] Implement trade-off detection: when a constraint conflict is found (e.g. OD ceiling forces a stress/safety-factor tension), have Agent 1 (or a new dedicated negotiation step) phrase at least one explicit trade-off via the LLM (short structured output, per ADR-6 judgment-point rule) and surface the affected quantities.
- [ ] Carry the user's trade-off choice forward into `AgentState` so it feeds geometry/compliance without re-asking prior answers.
- [ ] Add an iterative-refinement entry point: a follow-up instruction on a completed design (e.g. "make it cheaper", "use stainless") resumes the checkpointed thread and produces a revised ranked proposal referencing the previous `final_report`.
- [ ] Update `app/api/v1/design.py` endpoints to expose the new resume-based clarify contract (keep the existing route path if possible to minimize frontend churn; version it if the request/response shape changes incompatibly).
- [ ] Update `frontend/src/components/ClarificationDialog.tsx` and `frontend/src/services/design-service.ts` to drive the new per-turn resume flow instead of the old single-shot concatenate-and-resubmit pattern.
- [ ] Add median clarification-exchanges tracking (count turns to a complete spec) persisted on `design_projects` or computed from checkpoint history, to support the PRD success metric.
- [ ] Update `tests/test_workflow.py` and `tests/test_design_service.py` for interrupt/resume behavior (multi-turn conversation, trade-off carry-forward, iterative refinement).

**Acceptance criteria (P1-1, P1-2, P1-3)**
- The system can ask a follow-up, receive an answer, and ask a further follow-up in the same session without re-running the pipeline from scratch (P1-1).
- Conversation state persists across turns, surviving a process restart within the session window (P1-1).
- Median clarification exchanges to a complete spec is tracked and reported (P1-1).
- When a constraint conflict is detected, the system presents at least one explicit trade-off with the affected quantities (P1-2); the user's choice is carried forward without re-entering prior answers (P1-2).
- A follow-up instruction on a completed design produces a revised ranked proposal referencing the previous one (P1-3).

**Test strategy**
- New: `tests/test_workflow.py` cases for interrupt-then-resume continuing from a checkpoint (not from `START`), verified via a fresh graph invocation reusing `thread_id`.
- New: `tests/test_design_service.py` cases for the resume-based clarify endpoint contract.
- New: trade-off detection/phrasing unit test in `tests/test_agents.py` (Agent 1), with the LLM call mocked to isolate deterministic conflict-detection logic from LLM phrasing.
- Regression: full `pytest tests/ -q --ignore=tests/test_standards_ingestion.py`.
- **Exe smoke test required** — SDD packaging table: "SQLite saver uses already-bundled aiosqlite/sqlite3; add a writable checkpoint DB path under the exe's data dir. Low risk," but still a new writable-path dependency inside the frozen exe — verify a checkpoint round-trip (ask → answer → resume) inside the built `.exe`.

**Rollback/risk note**
- SDD risk: "Checkpointer write path in `.exe` (read-only install dir) → conversation persistence breaks." Mitigation: checkpoint DB must live in the Phase 0 per-user writable data dir, never next to the exe binary. If resume logic regresses, the old concatenate-and-rerun path in `design_service.py` should be kept behind a feature flag for one release before deletion, to allow fast rollback.

---

## Phase 4 — Engineering Depth (Extension & Torsion Springs)

**Goal.** Extend the strategy-pattern engines introduced in Phase 0 to support extension springs (initial tension, hook/end stress) and torsion springs (moment, angular deflection, leg geometry), plus tolerance outputs and type-specific compliance checks.

**Deliverables**
- `GeometryEngine` / `ComplianceEngine` strategy interfaces selected by `spring_type`.
- Extension engine (initial tension, hook-region stress).
- Torsion engine (moment/angle-driven, leg geometry).
- Dimensional tolerance outputs and type-specific normative checks.

**Task checklist**
- [ ] Define `GeometryEngine` and `ComplianceEngine` strategy interfaces (e.g. `app/tools/engines/base.py`) with the compression logic in `app/tools/geometry.py` / `app/tools/compliance.py` refactored to implement them as the reference implementation — no behavior change to compression.
- [ ] Add engine selection by `spring_type` in `app/agents/agent2_design.py` (geometry) and `app/agents/agent4_compliance.py` (compliance), dispatching to the compression engine by default to preserve current behavior.
- [ ] Implement `ExtensionGeometryEngine` (`app/tools/engines/extension.py`): solve using `SpringGeometry`'s existing fields plus initial tension, validated against a textbook worked example before enabling in the UI (per SDD risk mitigation).
- [ ] Implement `ExtensionComplianceEngine`: hook/end-region stress checks specific to extension springs.
- [ ] Implement `TorsionGeometryEngine` (`app/tools/engines/torsion.py`): solve using the already-present `torsion_moment_n_mm` and `angular_deflection_deg` fields on `SpringGeometry`, plus leg geometry.
- [ ] Implement `TorsionComplianceEngine`: moment/angle-based normative checks.
- [ ] Add dimensional tolerance fields to `SpringGeometry` (`app/schemas/state.py`) and populate them per applicable standard for all three supported types.
- [ ] Wire `spring_type`-aware routing through `app/agents/agent6_orchestrator.py` if the redesign loop needs type-specific directive handling.
- [ ] Update `frontend/src/components/GeometryTable.tsx`, `frontend/src/components/ComplianceCard.tsx`, and `frontend/src/components/Spring3DViewer.tsx` to render extension/torsion-specific fields (initial tension, hook geometry, leg angle) instead of assuming compression-only geometry.
- [ ] Add worked-example regression tests: `tests/test_tools.py` cases comparing extension and torsion engine outputs against known textbook values (tolerance-bounded assertions).

**Acceptance criteria (P3-1, P3-2, P3-3)**
- Geometry + compliance produce a valid extension design for a representative case, including initial tension and hook-region stress checks (P3-1).
- Geometry + compliance produce a valid torsion design using moment/angle inputs (P3-2).
- Output includes dimensional tolerances per applicable standard (P3-3).
- Compliance covers at least the current checks plus type-specific checks for extension/torsion (P3-3).

**Test strategy**
- New: `tests/test_tools.py` worked-example tests per engine (extension, torsion) with known-good textbook results as ground truth — required before enabling either type in the UI, per SDD risk mitigation.
- New: strategy-dispatch unit tests confirming `spring_type` selects the correct engine and compression behavior is unchanged.
- Regression: `tests/test_agents.py`, `tests/test_workflow.py` full suite (compression path must show zero output drift).
- No exe-specific packaging impact per SDD table ("None; `launcher.spec` `datas` already ships the whole `app/` package") — standard smoke test only, not a dedicated gate.

**Rollback/risk note**
- SDD risk: "Extension/torsion physics errors → wrong, unsafe designs." Mitigation: validate new engines against textbook worked examples before enabling in the UI; ship behind a type-availability flag so extension/torsion can be disabled instantly if a physics defect is found post-release without touching the compression path.

---

## Phase 5 — Quotation & Commercial Model

**Goal.** Turn the existing per-spring cost model into a customer-ready quotation: lot-size amortization, margin, price tiers, and a quote document — depends on Phase 1 so tier pricing reflects real, DB-sourced material costs.

**Deliverables**
- Cost model extended with setup amortization by lot size and configurable margin.
- At least two lot-size price tiers per option.
- Customer-ready quote document export (ranked options, per-tier price, validity date, cited standards).
- Cost parameters (margin, setup cost, tiers) stored as admin-editable settings.

**Task checklist**
- [ ] Add a settings/config store for cost parameters (setup cost, margin, lot tiers) — new table or extension of an existing settings mechanism, with defaults matching current single-unit behavior (per SDD risk mitigation: "make cost parameters explicit data with defaults matching current behavior").
- [ ] Extend `commercial_scoring_tool` (`app/tools/commercial.py`) with `total_unit_cost(lot_size) = material + manufacturing_ops + setup_cost / lot_size` and `unit_price = total_unit_cost * (1 + margin)`.
- [ ] Compute at least two lot tiers (e.g. 100 / 1,000 / 10,000) per ranked option.
- [ ] Extend `CommercialScore` and `final_report` (`app/schemas/state.py`) with per-lot-tier unit price, margin, and setup-amortization fields.
- [ ] Add a `quote_snapshot` JSON field on `design_projects` (`app/db/models.py` + Alembic migration) to persist the exact cost parameters used at quote time, so historical quotes remain reproducible even if settings change later.
- [ ] Add admin API for cost-parameter settings (extend `/api/v1/materials`-style CRUD pattern or add `/api/v1/settings/commercial`) plus a minimal frontend settings view.
- [ ] Extend `app/services/export_service.py` with a customer-ready quote document generator (ranked options, per-tier unit price, validity date, cited standards from Phase 2 if available) reusing the existing PDF export stack — no new native dependency.
- [ ] Add a quote-export endpoint in `app/api/v1/design.py` alongside the existing PDF/DXF export, and wire a "Download Quote" action in `frontend/src/components/DesignResult.tsx` / `frontend/src/components/ProposalsTable.tsx`.
- [ ] Update `tests/test_tools.py` (commercial scoring with tiers/margin) and `tests/test_api.py` (quote export endpoint) plus a snapshot test verifying default parameters reproduce today's per-spring cost exactly (zero silent drift).

**Acceptance criteria (P5-1)**
- Cost model includes setup amortization by lot size, margin, and at least two lot tiers (P5-1).
- Exported quote document lists ranked options with per-tier unit price and a validity date (P5-1).

**Test strategy**
- New: `tests/test_tools.py` cases for `total_unit_cost`/`unit_price` across tiers, plus a regression case asserting default settings reproduce the pre-Phase-5 single-unit cost bit-for-bit.
- New: `tests/test_api.py` case for the quote-export endpoint (content includes tiers, validity date, standards citation when present).
- Regression: `tests/test_agents.py` (Agent 5) full suite.
- No dedicated exe packaging risk per SDD table ("None new; reuse existing export stack") — standard smoke test covers PDF generation inside the exe.

**Rollback/risk note**
- SDD risk: "Cost-model changes silently alter existing quotes → commercial trust." Mitigation: cost parameters are explicit data with defaults matching current behavior, and `quote_snapshot` persists the parameters used per quote so past quotes are never silently recalculated. Rollback is a settings-default revert, not a code revert.

---

## Phase 6 — Autonomy & Memory

**Goal.** Make redesign iterations LLM-reasoned with grounded rationale, add per-option trade-off explanations, and add cross-session design-history search with won/lost outcome tagging — the "feels agentic" polish layer.

**Deliverables**
- LLM-generated, failure-mode-grounded rationale in redesign directives.
- Human-readable rationale per ranked option in `final_report`.
- Structured-output enforcement for requirement extraction with a regex fallback.
- Offline-mode toggle that disables cloud fallback.
- Extraction-quality eval harness.
- Design-history similarity search + won/lost outcome tagging.

**Task checklist**
- [ ] Add JSON-schema/function-calling structured output enforcement to the requirement-extraction call in `app/agents/agent1_requirements.py`, keeping the existing regex extractor as a deterministic fallback when the model omits/malforms fields (ADR-6).
- [ ] Add an "offline mode" flag (`app/core/settings.py`) that disables cloud provider rotation in `app/core/llm_factory.py`, forcing local-Ollama-only completion for the `.exe` default.
- [ ] Build a small extraction-quality eval harness (`tests/test_extraction_eval.py` or `scripts/eval_extraction.py`) scoring field-level accuracy against a gold requirements set, run against the local model as a prompt/model-change regression gate.
- [ ] Extend the redesign-directive generation in `app/agents/agent6_orchestrator.py` (or wherever directives are currently constructed from fixed rules) with an LLM-generated rationale grounded in the specific failure modes from `ComplianceReport`, sized for reliable 7B output (short structured justification, not open-ended planning).
- [ ] Add a per-option rationale field to `CommercialScore`/`final_report` (`app/schemas/state.py`) populated by Agent 5 with a short LLM-generated cost-vs-durability-vs-compactness explanation for each ranked option.
- [ ] Add `outcome` (won/lost/pending) and a requirement feature/embedding key column to `design_projects` (`app/db/models.py` + Alembic migration).
- [ ] Add a design-history similarity search reusing the Phase 2 vector/FTS store (or a lightweight SQL similarity over normalized requirement fields if that store doesn't fit) — new `app/services/design_history_service.py` and `/api/v1/designs/search` endpoint.
- [ ] Add an outcome-tagging endpoint (`PATCH /api/v1/design/{id}/outcome`) and surface "similar past design found" at requirement time in `app/agents/agent1_requirements.py`, plus a frontend hint in `frontend/src/components/DesignHistory.tsx`.
- [ ] Update `tests/test_agents.py` (redesign rationale, per-option rationale with mocked LLM), `tests/test_repositories.py` or a new `tests/test_design_history.py` (similarity search, outcome tagging), and full regression run.

**Acceptance criteria (P4-1, P4-2, P4-3)**
- Redesign directives include an LLM-generated rationale grounded in the failure modes, within what a local 7B model can reliably produce (P4-1).
- Each ranked option in `final_report` includes a concise rationale — cost vs durability vs compactness (P4-2).
- Past designs are searchable by requirement similarity; a match surfaces in a new session (P4-3).

**Test strategy**
- New: redesign-rationale and per-option-rationale unit tests in `tests/test_agents.py` with the LLM call mocked to isolate deterministic logic from generated text.
- New: extraction eval harness run as part of CI (or a manual gate) scoring field-level accuracy — becomes a standing gate on prompt/model changes per ADR-6.
- New: `tests/test_design_history.py` for similarity search and outcome tagging against a seeded `design_projects` fixture.
- Regression: full `pytest tests/ -q --ignore=tests/test_standards_ingestion.py`.
- **Exe smoke test required** for the offline-mode toggle specifically — SDD packaging table: "None; Ollama is external. Ensure offline mode disables cloud cleanly." Verify a full run completes in the built `.exe` with cloud providers disabled and no network access.

**Rollback/risk note**
- SDD risk: "7B model malforms structured output → bad extraction, bad quotes." Mitigation already designed in: JSON-schema/function-calling enforcement plus the regex fallback plus the extraction eval harness gate. If the offline-mode toggle regresses cloud rotation for online users, ship it as an explicit opt-in setting (not a silent default flip) so server-mode users are unaffected.

---

## Notes / Inconsistencies Found

- **PRD says "no rewrite" of the multi-option materials/commercial feature (Non-Goal), while ADR-5 modifies `CommercialScore` and `final_report` schemas.** This is not a real conflict — the PRD explicitly frames Pillar-adjacent extension as allowed ("extended, not replaced"), and ADR-5's changes are additive fields (tier price, margin, rationale), not a rewrite of the ranking logic. Flagged here only so reviewers don't misread schema growth as scope creep; the plan proceeds as scoped in the SDD.
- No other contradictions found between `PRD.md` and `SDD.md`; the SDD's Phase Mapping section was followed exactly (Phase 0 through Phase 6, same scope per phase, same dependency ordering).
