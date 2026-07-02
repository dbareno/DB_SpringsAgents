'use client';

import { useState, type FormEvent } from 'react';
import { Send } from 'lucide-react';
import Button from '@/components/ui/Button';
import Input from '@/components/ui/Input';
import Card from '@/components/ui/Card';

interface DesignFormProps {
  onSubmit: (userInput: string, maxIterations: number) => Promise<void>;
  isLoading: boolean;
}

export default function DesignForm({ onSubmit, isLoading }: DesignFormProps) {
  const [userInput, setUserInput] = useState('');
  const [maxIterations, setMaxIterations] = useState(5);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!userInput.trim() || isLoading) return;
    await onSubmit(userInput.trim(), maxIterations);
  };

  return (
    <Card className="w-full max-w-2xl mx-auto">
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Input
          label="Descripción del resorte"
          placeholder="Ej: Necesito un resorte de compresión en acero al carbono, con diámetro exterior de 30mm, carga de 500N y una vida útil de 100,000 ciclos..."
          value={userInput}
          onChange={(e) => setUserInput(e.target.value)}
          rows={4}
          disabled={isLoading}
        />

        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="max-iterations"
            className="text-sm font-medium text-zinc-300"
          >
            Iteraciones máximas: {maxIterations}
          </label>
          <input
            id="max-iterations"
            type="range"
            min={1}
            max={15}
            value={maxIterations}
            onChange={(e) => setMaxIterations(Number(e.target.value))}
            disabled={isLoading}
            className="w-full h-2 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-blue-500 disabled:opacity-50"
          />
          <div className="flex justify-between text-xs text-zinc-500">
            <span>1</span>
            <span>15</span>
          </div>
        </div>

        <Button type="submit" isLoading={isLoading} className="self-end">
          <Send className="h-4 w-4" />
          Diseñar resorte
        </Button>
      </form>
    </Card>
  );
}
