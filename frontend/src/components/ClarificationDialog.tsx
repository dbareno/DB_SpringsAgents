'use client';

import { useState, type FormEvent } from 'react';
import { HelpCircle, Send } from 'lucide-react';
import Button from '@/components/ui/Button';
import Input from '@/components/ui/Input';
import Card from '@/components/ui/Card';

interface ClarificationDialogProps {
  questions: string[];
  onSubmit: (answers: string) => Promise<void>;
  isLoading: boolean;
}

export default function ClarificationDialog({
  questions,
  onSubmit,
  isLoading,
}: ClarificationDialogProps) {
  const [answers, setAnswers] = useState('');

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!answers.trim() || isLoading) return;
    await onSubmit(answers.trim());
  };

  if (!questions || questions.length === 0) return null;

  return (
    <Card className="w-full max-w-2xl mx-auto border-amber-700/50">
      <div className="flex items-start gap-3 mb-4">
        <HelpCircle className="h-6 w-6 text-amber-400 shrink-0 mt-0.5" />
        <div>
          <h3 className="text-lg font-semibold text-amber-300">
            Necesitamos más información
          </h3>
          <p className="text-sm text-zinc-400 mt-1">
            El agente de diseño necesita algunos detalles adicionales para completar el diseño.
          </p>
        </div>
      </div>

      <div className="mb-4 space-y-2">
        <p className="text-sm font-medium text-zinc-300">Preguntas:</p>
        <ul className="space-y-1.5">
          {questions.map((q, i) => (
            <li
              key={i}
              className="flex items-start gap-2 text-sm text-zinc-400"
            >
              <span className="text-amber-400 mt-px shrink-0">•</span>
              <span>{q}</span>
            </li>
          ))}
        </ul>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Input
          label="Tus respuestas"
          placeholder="Respondé cada pregunta de forma clara y concisa..."
          value={answers}
          onChange={(e) => setAnswers(e.target.value)}
          rows={4}
          disabled={isLoading}
        />
        <Button type="submit" isLoading={isLoading} className="self-end">
          <Send className="h-4 w-4" />
          Enviar respuestas
        </Button>
      </form>
    </Card>
  );
}
