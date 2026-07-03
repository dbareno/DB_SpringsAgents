# Plan de Mejora — Spring Design Agent Pipeline

Cada fase produce un `.exe` funcional que probamos antes de pasar a la siguiente.

---

## Fase 1 — Optimizer robusto (Agent 2)

**Problema**: `scipy.optimize.minimize` con SLSQP falla con `Positive directional derivative for linesearch`. El analytical fallback da geometría válida (Sf≈1.34) pero NO es óptima en peso/costo y no explora el espacio de diseño.

**Solución**: Migrar a `scipy.optimize.differential_evolution` — optimizador global sin gradientes.
- No necesita gradientes → no falla por derivadas mal condicionadas
- Explora todo el espacio (d, D, n_a) en vez de una solución cerrada
- Maneja naturalmente constraints mixtos (diámetro, coils, longitud)

**Criterio de éxito**:
- `optimizer_converged: true` en la respuesta
- Volumen de alambre igual o menor que con analytical fallback
- Tiempo de cómputo aceptable (< 5s)

**Archivos a modificar**: `app/tools/spring_tools.py`

---

## Fase 2 — Rediseño inteligente con feedback numérico

**Problema**: El loop de rediseño depende del LLM para adivinar cuánto ajustar geometría. Agent 2 recibe directives vagas ("increase wire diameter") y prueba valores al azar, desperdiciando iteraciones.

**Solución**: Crear `redesign_advisor_tool` que traduce directives cualitativas a ajustes cuantitativos.
- "Insufficient shear safety factor: 1.05 < 1.30" → `{wire_diameter_mm: +8.2%}`
- "Buckling risk: slenderness 6.2 > 5.26" → `{free_length_mm: -10%, mean_coil_diameter_mm: +5%}`
- Agent 2 aplica los ajustes exactos sin LLM → converge en 1-2 iteraciones

**Criterio de éxito**:
- Loop de rediseño converge en ≤ 2 iteraciones
- No se necesita LLM para calcular ajustes

**Archivos a modificar**: `app/tools/spring_tools.py`, `app/agents/agent2_design.py`

---

## Fase 3 — Commercial Optimizer con costos reales (Agent 5)

**Problema**: Agent 5 solo calcula peso de alambre × $/kg. No considera manufactura, heat treat, shot peening, lead time.

**Solución**: Agregar modelo de costos multi-factor:
- **Arrollado**: función de d/D (diámetros pequeños o grandes encarecen)
- **Tratamiento térmico**: necesario si Sy > 1500 MPa o después de arrollado en frío
- **Shot peening**: si cyclic_load=True, mejora vida fatiga pero agrega costo
- **Material**: el costo base del catálogo
- Score compuesto: (costo total + vida estimada) normalizado

**Criterio de éxito**:
- Múltiples propuestas con ranking realista
- Propuesta #1 es la de mejor relación costo-vida

**Archivos a modificar**: `app/agents/agent5_commercial.py`, `app/tools/spring_tools.py`

---

## Fase 4 — Export a PDF/DXF (Frontend)

**Problema**: El frontend solo muestra resultados en JSON/texto. No hay plano técnico, PDF, ni DXF para CAD.

**Solución**:
- Botón "Exportar PDF" → genera plano técnico con reportlab: tabla de geometría, material, compliance y comercial
- Botón "Exportar DXF" → genera archivo DXF con silueta del resorte en vista lateral (zigzag), línea de centro, cotas OD/L0/d y bloque de título
- Backend: `app/services/export_service.py` con `_build_pdf()` (reportlab) y `_build_dxf()` (ezdxf + NamedTemporaryFile)
- Endpoints REST: `GET /{session_id}/export/pdf` y `GET /{session_id}/export/dxf`
- Frontend: `DesignResult.tsx` con botones PDF/DXF, llaman a `window.open` directo

**Criterio de éxito**:
- PDF descargable con plano legible (✔ 4526 bytes, 4 tablas + footer)
- DXF importable en CAD (✔ 22901 bytes, capas SPRING/CENTER/DIM/BORDER)

**Archivos a modificar**: `app/services/export_service.py` (nuevo), `app/api/v1/design.py` (2 endpoints), `frontend/src/components/DesignResult.tsx` (botones), `frontend/src/services/design-service.ts` (métodos export)

---

## Trazabilidad

| Fase | Estado       | .exe build | Tests       | Commit tag               |
|------|-------------|------------|-------------|--------------------------|
| 1    | ✅ Completada | OK         | 117✔ / 6✘¹ | `phase-1-optimizer`      |
| 2    | ✅ Completada | OK         | 128✔ / 0✘  | `phase-2-redesign`       |
| 3    | ✅ Completada | OK         | 123✔ / 0✘  | `phase-3-commercial`     |
| 4    | ✅ Completada | OK         | 123✔ / 0✘  | `phase-4-export`         |

> ¹ Los 6 tests deselected son pre-existentes que requieren Ollama corriendo o base de datos SQL.
