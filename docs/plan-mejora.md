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
- Botón "Exportar PDF" → genera plano técnico con cotas, tabla de parámetros, norma aplicable
- Botón "Exportar DXF" → genera archivo DXF con la geometría del resorte
- Usar weasyprint / reportlab en backend o librería JS en frontend

**Criterio de éxito**:
- PDF descargable con plano legible
- DXF importable en CAD (Fusion 360, SolidWorks, FreeCAD)

**Archivos a modificar**: Frontend (React/Next.js), backend (endpoint de export)

---

## Trazabilidad

| Fase | Estado       | .exe build | Tests       | Commit tag            |
|------|-------------|------------|-------------|-----------------------|
| 1    | ✅ Completada | OK         | 117✔ / 11✘¹ | `phase-1-optimizer`   |
| 2    | 🔲 Pendiente |            |             | `phase-2-redesign`    |
| 3    | 🔲 Pendiente |            |             | `phase-3-commercial`  |
| 4    | 🔲 Pendiente |            |             | `phase-4-export`      |

> ¹ Los 11 failures son pre-existentes (API contract async/Pydantic v2/env mismatch), no relacionados con esta fase.
