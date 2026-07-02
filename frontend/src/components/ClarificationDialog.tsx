'use client';

import { useState, type FormEvent } from 'react';
import { HelpCircle, Send } from 'lucide-react';
import Button from '@/components/ui/Button';
import Card from '@/components/ui/Card';

interface ClarificationDialogProps {
  questions: string[];
  onSubmit: (answers: string[]) => Promise<void>;
  isLoading: boolean;
}

export default function ClarificationDialog({
  questions,
  onSubmit,
  isLoading,
}: ClarificationDialogProps) {
  // Estado inicial: un campo vacío por cada pregunta
  const [answers, setAnswers] = useState<string[]>(
    new Array(questions.length).fill('')
  );

  const handleAnswerChange = (index: number, value: string) => {
    setAnswers((prev) => {
      const next = [...prev];
      next[index] = value;
      return next;
    });
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    // Filtrar respuestas vacías
    const nonEmpty = answers.map((a) => a.trim()).filter((a) => a.length > 0);
    if (nonEmpty.length === 0 || isLoading) return;
    await onSubmit(nonEmpty);
  };

  const allFilled = answers.every((a) => a.trim().length > 0);

  if (!questions || questions.length === 0) return null;

  return (
    <Card className="w-full max-w-2xl mx-auto border-amber-700/50">
      <div className="flex items-start gap-3 mb-4">
        <HelpCircle className="h-6 w-6 text-amber-400 shrink-0 mt-0.5" />
        <div>
          <h3 className="text-lg font-semibold text-amber-300">
            Información adicional requerida
          </h3>
          <p className="text-sm text-zinc-400 mt-1">
            El agente necesita completar algunos datos para continuar con el diseño.
            Responde cada pregunta en su propio campo.
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* Una card por pregunta */}
        <div className="space-y-3">
          {questions.map((question, i) => (
            <div
              key={i}
              className="rounded-lg border border-zinc-700/50 bg-zinc-900/50 p-3"
            >
              <label className="block text-sm font-medium text-zinc-300 mb-1.5">
                <span className="text-amber-400 mr-1">{i + 1}.</span>
                {question}
              </label>
              <textarea
                value={answers[i]}
                onChange={(e) => handleAnswerChange(i, e.target.value)}
                placeholder="Escribe tu respuesta aquí..."
                rows={2}
                disabled={isLoading}
                className="w-full rounded-lg border border-zinc-700 bg-[#0d1117] px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 transition-colors focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between pt-2">
          <span className="text-xs text-zinc-500">
            {answers.filter((a) => a.trim().length > 0).length} de{' '}
            {questions.length} respondidas
          </span>
          <Button
            type="submit"
            isLoading={isLoading}
            disabled={!allFilled}
          >
            <Send className="h-4 w-4" />
            Enviar respuestas
          </Button>
        </div>
      </form>
    </Card>
  );
}
