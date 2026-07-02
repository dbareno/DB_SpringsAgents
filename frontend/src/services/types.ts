// ─── Tipos de respuesta de la API ───────────────────────────────────────────

export interface ThreeJSParams {
  wireRadius: number;
  coilRadius: number;
  totalCoils: number;
  height: number;
  tubeSegments: number;
  radialSegments: number;
}

export interface Summary {
  spring_type: string;
  material: string;
  applicable_standard: string;
  approved: boolean;
}

export interface Geometry {
  wire_diameter_mm: number;
  mean_coil_diameter_mm: number;
  outer_diameter_mm: number;
  inner_diameter_mm: number;
  active_coils: number;
  total_coils: number;
  free_length_mm: number;
  pitch_mm: number;
  spring_index: number;
  spring_rate_n_mm: number;
  wahl_factor: number;
  corrected_shear_stress_mpa: number;
  slenderness_ratio: number;
  torsion_moment_n_mm: number | null;
  angular_deflection_deg: number | null;
}

export interface Compliance {
  approved: boolean;
  safety_factor_shear: number;
  safety_factor_buckling: number;
  safety_factor_fatigue: number | null;
  applicable_standard: string;
  failure_modes: string[];
  redesign_directives: string[];
}

export interface Proposal {
  proposal_id: string;
  rank: number;
  composite_score: number;
  wire_mass_kg: number;
  material_cost_usd: number;
  estimated_life_cycles: number;
  three_js_params: ThreeJSParams;
}

export interface ChartData {
  proposal_id: string;
  rank: number;
  composite_score: number;
  material_cost_usd: number;
  estimated_life_cycles: number;
  safety_factor_shear: number;
  safety_factor_buckling: number;
  wire_mass_kg: number;
}

export interface ThreeJSSceneSpring {
  wireRadius: number;
  coilRadius: number;
  totalCoils: number;
  height: number;
  tubeSegments: number;
  radialSegments: number;
}

export interface ThreeJSScene {
  spring: ThreeJSSceneSpring;
  material_color: string;
  background: string;
}

export interface Report {
  summary: Summary;
  geometry: Geometry;
  compliance: Compliance;
  commercial: {
    ranked_proposals: Proposal[];
    chart_data: ChartData[];
  };
  three_js_scene: ThreeJSScene;
  generated_at: string;
}

export interface DesignResponse {
  session_id: string;
  status:
    | 'processing'
    | 'approved'
    | 'needs_clarification'
    | 'iteration_limit_reached'
    | 'error';
  report: Report | null;
  clarification_questions: string[] | null;
}

export interface StepProgress {
  session_id: string;
  status: string;
  current_step: string | null;
  progress_pct: number;
  error: string | null;
}

export interface HealthResponse {
  status: string;
  version: string;
}

// ─── Tipos específicos del frontend ─────────────────────────────────────────

export type FormStatus =
  | 'idle'
  | 'loading'
  | 'clarifying'
  | 'success'
  | 'error'
  | 'backend_offline';

// ─── Pipeline steps ─────────────────────────────────────────────────────────
export interface PipelineStep {
  id: string;
  label: string;
  description: string;
  icon: string;
}

export const PIPELINE_STEPS: PipelineStep[] = [
  {
    id: 'requirements_analyst',
    label: 'Requisitos',
    description: 'Analizando requerimientos del resorte',
    icon: '📋',
  },
  {
    id: 'materials_engineer',
    label: 'Materiales',
    description: 'Seleccionando material óptimo',
    icon: '🔬',
  },
  {
    id: 'design_engineer',
    label: 'Diseño',
    description: 'Calculando geometría del resorte',
    icon: '📐',
  },
  {
    id: 'normative_inspector',
    label: 'Normativa',
    description: 'Verificando cumplimiento normativo',
    icon: '✅',
  },
  {
    id: 'commercial_optimiser',
    label: 'Comercial',
    description: 'Optimizando costo y rendimiento',
    icon: '💰',
  },
];
