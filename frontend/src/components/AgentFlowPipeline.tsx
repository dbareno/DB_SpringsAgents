'use client';

import { useMemo } from 'react';
import clsx from 'clsx';
import {
  ClipboardList,
  Beaker,
  Ruler,
  ShieldCheck,
  Banknote,
  Loader2,
  Check,
  X,
} from 'lucide-react';
import type { PipelineStep } from '@/services/types';
import { PIPELINE_STEPS } from '@/services/types';
import Card from '@/components/ui/Card';

interface AgentFlowPipelineProps {
  currentStep: string | null;
  error?: string | null;
}

const STEP_ICONS: Record<string, React.ReactNode> = {
  requirements_analyst: <ClipboardList className="h-5 w-5" />,
  materials_engineer: <Beaker className="h-5 w-5" />,
  design_engineer: <Ruler className="h-5 w-5" />,
  normative_inspector: <ShieldCheck className="h-5 w-5" />,
  commercial_optimiser: <Banknote className="h-5 w-5" />,
};

/** Detecta el paso actual del pipeline a partir del current_step del backend. */
function getActiveStepIndex(currentStep: string | null): number {
  if (!currentStep) return -1;

  // Mapear pasos del backend a índices del pipeline
  const stepMap: Record<string, number> = {
    requirements_analyst: 0,
    materials_engineer: 1,
    design_engineer: 2,
    normative_inspector: 3,
    commercial_optimiser: 4,
  };

  // Pasos de redesign loop: mostrar como "Diseño" activo
  if (
    currentStep.includes('redesign') ||
    currentStep === 'increment_iteration'
  ) {
    return 2; // design step
  }

  // Redesign iteration counter -> design step
  if (currentStep.startsWith('redesign_iteration_')) {
    return 2;
  }

  // Terminal / awaiting clarification
  if (currentStep === 'awaiting_clarification') {
    return 0; // keep requirements highlighted
  }

  return stepMap[currentStep] ?? -1;
}

export default function AgentFlowPipeline({
  currentStep,
  error,
}: AgentFlowPipelineProps) {
  const activeIndex = useMemo(
    () => getActiveStepIndex(currentStep),
    [currentStep]
  );

  const iterationMatch = currentStep?.match(/redesign_iteration_(\d+)/);
  const iterationLabel = iterationMatch
    ? `Iteración ${iterationMatch[1]}`
    : null;

  return (
    <Card className="w-full max-w-3xl mx-auto">
      <div className="flex flex-col gap-2">
        {/* Título */}
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
            Pipeline de agentes
          </h3>
          {iterationLabel && (
            <span className="text-xs font-medium text-amber-400 bg-amber-400/10 px-2 py-0.5 rounded-full">
              {iterationLabel}
            </span>
          )}
        </div>

        {/* Barra de progreso */}
        <div className="relative h-1.5 bg-zinc-800 rounded-full overflow-hidden">
          <div
            className={clsx(
              'absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out',
              error ? 'bg-red-500' : 'bg-blue-500'
            )}
            style={{
              width: `${
                activeIndex >= 0
                  ? ((activeIndex + 1) / PIPELINE_STEPS.length) * 100
                  : 0
              }%`,
            }}
          />
        </div>

        {/* Steps */}
        <div className="grid grid-cols-5 gap-1 mt-1">
          {PIPELINE_STEPS.map((step: PipelineStep, index: number) => {
            const isActive = index === activeIndex;
            const isCompleted = index < activeIndex;
            const isError = !!error;

            return (
              <div
                key={step.id}
                className={clsx(
                  'flex flex-col items-center gap-1.5 p-2 rounded-lg transition-all duration-500',
                  isActive && !isError && 'bg-blue-500/10 ring-1 ring-blue-500/30',
                  isCompleted && !isError && 'bg-zinc-800/50',
                  isError && 'opacity-50'
                )}
              >
                {/* Ícono */}
                <div
                  className={clsx(
                    'flex items-center justify-center w-8 h-8 rounded-full transition-all duration-500',
                    isCompleted && !isError
                      ? 'bg-green-500/20 text-green-400'
                      : isActive && !isError
                      ? 'bg-blue-500/20 text-blue-400'
                      : 'bg-zinc-800 text-zinc-600'
                  )}
                >
                  {isError ? (
                    <X className="h-4 w-4 text-red-400" />
                  ) : isCompleted ? (
                    <Check className="h-4 w-4" />
                  ) : isActive ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    STEP_ICONS[step.id]
                  )}
                </div>

                {/* Label */}

                <span
                  className={clsx(
                    'text-xs font-medium text-center leading-tight',
                    isCompleted && !isError && 'text-zinc-400',
                    isActive && !isError && 'text-blue-300',
                    !isActive && !isCompleted && 'text-zinc-600'
                  )}
                >
                  {step.label}
                </span>
              </div>
            );
          })}
        </div>

        {/* Descripción del paso actual */}
        <div className="text-center mt-1">
          {error ? (
            <p className="text-xs text-red-400 animate-pulse">
              Error en el proceso: {error}
            </p>
          ) : activeIndex >= 0 ? (
            <p className="text-xs text-zinc-500 animate-pulse">
              {PIPELINE_STEPS[activeIndex].description}
              {iterationLabel && ` — ${iterationLabel}`}
            </p>
          ) : (
            <p className="text-xs text-zinc-600">
              Iniciando proceso de diseño...
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
