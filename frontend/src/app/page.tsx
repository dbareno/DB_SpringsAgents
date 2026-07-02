'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Rotate3d, WifiOff, AlertCircle } from 'lucide-react';
import DesignForm from '@/components/DesignForm';
import ClarificationDialog from '@/components/ClarificationDialog';
import DesignResult from '@/components/DesignResult';
import DesignHistory, { addToHistory } from '@/components/DesignHistory';
import AgentFlowPipeline from '@/components/AgentFlowPipeline';
import Spinner from '@/components/ui/Spinner';
import Button from '@/components/ui/Button';
import Card from '@/components/ui/Card';
import { DesignService } from '@/services/design-service';
import { NetworkError } from '@/services/api-client';
import type { DesignResponse, FormStatus } from '@/services/types';

const designService = new DesignService();

const POLL_INTERVAL_MS = 1500;

export default function HomePage() {
  const [status, setStatus] = useState<FormStatus>('idle');
  const [response, setResponse] = useState<DesignResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [lastInput, setLastInput] = useState('');

  // ─── Pipeline visual ─────────────────────────────────────────────────────

  const [currentPipelineStep, setCurrentPipelineStep] = useState<
    string | null
  >(null);
  const [pipelineError, setPipelineError] = useState<string | null>(null);
  const activeSessionId = useRef<string | null>(null);

  // ─── Polling: monitorea el progreso del grafo ────────────────────────────

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (sessionId: string) => {
      stopPolling();
      activeSessionId.current = sessionId;

      pollRef.current = setInterval(async () => {
        try {
          const progress = await designService.getDesignStatus(sessionId);

          if (progress.status === 'processing') {
            // Todavía ejecutando: actualizar pipeline
            setCurrentPipelineStep(progress.current_step);
            return;
          }

          // Dejó de procesar: obtener resultado final
          stopPolling();
          activeSessionId.current = null;
          setCurrentPipelineStep(null);

          if (progress.status === 'error') {
            setStatus('error');
            setErrorMessage(
              progress.error ?? 'Error durante la ejecución del diseño.'
            );
            return;
          }

          // Obtener respuesta completa
          const result = await designService.getDesign(sessionId);

          if (result.status === 'needs_clarification') {
            setStatus('clarifying');
            setResponse(result);
          } else if (result.status === 'approved') {
            setStatus('success');
            setResponse(result);
            if (result.session_id) {
              addToHistory(
                result.session_id,
                lastInput.slice(0, 60) || 'Diseño completado'
              );
            }
          } else if (result.status === 'error') {
            setStatus('error');
            setErrorMessage(
              result.report?.compliance?.failure_modes?.join(', ') ??
                'El agente encontró un error durante el diseño.'
            );
            setResponse(result);
          } else {
            // iteration_limit_reached
            setStatus('error');
            setErrorMessage(
              'Se alcanzó el límite de iteraciones sin llegar a un diseño aprobado. ' +
                'Prueba con una descripción más detallada o aumenta las iteraciones.'
            );
            setResponse(result);
          }
        } catch (error) {
          // Error haciendo polling
          stopPolling();
          activeSessionId.current = null;
          setCurrentPipelineStep(null);

          if (error instanceof NetworkError) {
            setStatus('backend_offline');
            setErrorMessage(
              error instanceof Error ? error.message : 'Error de conexión.'
            );
          } else {
            setStatus('error');
            setErrorMessage(
              error instanceof Error
                ? error.message
                : 'Error al consultar el progreso del diseño.'
            );
          }
        }
      }, POLL_INTERVAL_MS);
    },
    [stopPolling, lastInput]
  );

  // Limpiar polling al desmontar
  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  // ─── Manejador del formulario principal ──────────────────────────────────

  const handleSubmitDesign = useCallback(
    async (userInput: string, maxIterations: number) => {
      setStatus('loading');
      setResponse(null);
      setErrorMessage(null);
      setCurrentPipelineStep(null);
      setPipelineError(null);
      setLastInput(userInput);

      try {
        const result = await designService.startDesign(
          userInput,
          maxIterations
        );

        if (result.status === 'processing') {
          // Iniciar polling de progreso
          startPolling(result.session_id);
        } else if (result.status === 'needs_clarification') {
          // Caso borde: el grafo terminó antes de que empecemos a pollear
          setStatus('clarifying');
          setResponse(result);
        } else {
          setStatus('error');
          setErrorMessage('Respuesta inesperada del backend.');
        }
      } catch (error) {
        if (error instanceof NetworkError) {
          setStatus('backend_offline');
          setErrorMessage(
            error instanceof Error ? error.message : 'Error de conexión.'
          );
        } else {
          setStatus('error');
          setErrorMessage(
            error instanceof Error
              ? error.message
              : 'Error inesperado al comunicarse con el backend.'
          );
        }
      }
    },
    [startPolling]
  );

  // ─── Manejador de clarificación ──────────────────────────────────────────

  const handleClarify = useCallback(
    async (answers: string[]) => {
      if (!response?.session_id) return;
      setStatus('loading');
      setResponse(null);
      setCurrentPipelineStep(null);
      setPipelineError(null);

      try {
        const result = await designService.clarifyDesign(
          response.session_id,
          answers
        );

        if (result.status === 'processing') {
          startPolling(result.session_id);
        } else if (result.status === 'needs_clarification') {
          setStatus('clarifying');
          setResponse(result);
        } else if (result.status === 'approved') {
          setStatus('success');
          setResponse(result);
          if (result.session_id) {
            addToHistory(result.session_id, lastInput.slice(0, 60));
          }
        } else {
          setStatus('error');
          setErrorMessage(
            result.report?.compliance?.failure_modes?.join(', ') ??
              'El diseño no pudo completarse después de la clarificación.'
          );
          setResponse(result);
        }
      } catch (error) {
        if (error instanceof NetworkError) {
          setStatus('backend_offline');
          setErrorMessage(
            error instanceof Error ? error.message : 'Error de conexión.'
          );
        } else {
          setStatus('error');
          setErrorMessage(
            error instanceof Error
              ? error.message
              : 'Error al enviar las respuestas.'
          );
        }
      }
    },
    [response?.session_id, lastInput, startPolling]
  );

  // ─── Cargar diseño desde el historial ────────────────────────────────────

  const handleHistorySelect = useCallback(
    async (sessionId: string) => {
      setStatus('loading');
      setResponse(null);
      setErrorMessage(null);

      try {
        const result = await designService.getDesign(sessionId);
        if (result.status === 'approved') {
          setStatus('success');
          setResponse(result);
        } else {
          setStatus('error');
          setErrorMessage(
            'El diseño seleccionado no está disponible o no fue aprobado.'
          );
          setResponse(result);
        }
      } catch (error) {
        if (error instanceof NetworkError) {
          setStatus('backend_offline');
          setErrorMessage(
            error instanceof Error ? error.message : 'Error de conexión.'
          );
        } else {
          setStatus('error');
          setErrorMessage(
            error instanceof Error
              ? error.message
              : 'Error al recuperar el diseño.'
          );
        }
      }
    },
    []
  );

  const handleRetry = useCallback(() => {
    stopPolling();
    activeSessionId.current = null;
    setCurrentPipelineStep(null);
    setPipelineError(null);
    setStatus('idle');
    setResponse(null);
    setErrorMessage(null);
  }, [stopPolling]);

  // ─── Render ──────────────────────────────────────────────────────────────

  const showForm =
    status === 'idle' || (status === 'clarifying' && response?.clarification_questions);

  return (
    <div className="flex flex-col flex-1 gap-8">
      {/* Hero */}
      <header className="text-center py-8">
        <div className="inline-flex items-center justify-center rounded-2xl bg-blue-900/30 p-3 mb-4">
          <Rotate3d className="h-8 w-8 text-blue-400" />
        </div>
        <h1 className="text-3xl font-bold text-zinc-100 sm:text-4xl">
          Spring Design Agent
        </h1>
        <p className="mt-2 max-w-xl mx-auto text-zinc-500">
          Agente inteligente para el diseño, verificación y optimización de
          resortes helicoidales. Describe tu resorte en lenguaje natural y
          obtén un diseño completo con geometría, análisis de cumplimiento y
          propuestas comerciales.
        </p>
      </header>

      {/* Historial */}
      {status === 'idle' && <DesignHistory onSelect={handleHistorySelect} />}

      {/* Formulario principal */}
      {showForm && (
        <DesignForm onSubmit={handleSubmitDesign} isLoading={false} />
      )}

      {/* Pipeline visual durante la ejecución */}
      {(status === 'loading' || currentPipelineStep !== null) && (
        <div className="flex flex-col items-center gap-6 py-4">
          <AgentFlowPipeline
            currentStep={currentPipelineStep}
            error={pipelineError}
          />
          {!currentPipelineStep && (
            <div className="flex flex-col items-center gap-3">
              <Spinner size="lg" />
              <p className="text-sm text-zinc-500 animate-pulse">
                Iniciando proceso de diseño...
              </p>
            </div>
          )}
        </div>
      )}

      {/* Clarificación: preguntas separadas */}
      {status === 'clarifying' && response?.clarification_questions && (
        <ClarificationDialog
          questions={response.clarification_questions}
          onSubmit={handleClarify}
          isLoading={false}
        />
      )}

      {/* Éxito */}
      {status === 'success' && response?.report && (
        <DesignResult report={response.report} />
      )}

      {/* Backend offline */}
      {status === 'backend_offline' && (
        <Card className="w-full max-w-md mx-auto text-center border-red-800/50">
          <div className="flex flex-col items-center gap-3 py-4">
            <WifiOff className="h-10 w-10 text-red-400" />
            <h3 className="text-lg font-semibold text-red-300">
              Backend no disponible
            </h3>
            <p className="text-sm text-zinc-400">
              No se puede conectar con el servidor de diseño en{' '}
              <code className="text-zinc-300">
                {process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}
              </code>
            </p>
            <p className="text-xs text-zinc-600">
              Asegúrate de que el backend de FastAPI esté corriendo y reintenta.
            </p>
            <Button variant="outline" onClick={handleRetry}>
              Reintentar
            </Button>
          </div>
        </Card>
      )}

      {/* Error */}
      {status === 'error' && (
        <Card className="w-full max-w-md mx-auto border-red-800/50">
          <div className="flex flex-col items-center gap-3 py-4">
            <AlertCircle className="h-10 w-10 text-red-400" />
            <h3 className="text-lg font-semibold text-red-300">
              Error en el diseño
            </h3>
            <p className="text-sm text-zinc-400 text-center">
              {errorMessage}
            </p>
            <div className="flex gap-2">
              <Button variant="outline" onClick={handleRetry}>
                Volver a empezar
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
