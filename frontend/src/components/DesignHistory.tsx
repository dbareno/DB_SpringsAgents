'use client';

import { useEffect, useState } from 'react';
import { Clock, Trash2 } from 'lucide-react';
import Button from '@/components/ui/Button';
import Card from '@/components/ui/Card';

const STORAGE_KEY = 'spring_design_history';

interface HistoryEntry {
  session_id: string;
  label: string;
  timestamp: string;
}

function loadHistory(): HistoryEntry[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Ignora el error si localStorage no está disponible o está lleno
  }
}

interface DesignHistoryProps {
  onSelect: (sessionId: string) => void;
}

export default function DesignHistory({ onSelect }: DesignHistoryProps) {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);

  useEffect(() => {
    setEntries(loadHistory());
  }, []);

  const handleClear = () => {
    setEntries([]);
    localStorage.removeItem(STORAGE_KEY);
  };

  const handleSelect = (sessionId: string) => {
    onSelect(sessionId);
  };

  if (entries.length === 0) return null;

  return (
    <Card className="w-full max-w-2xl mx-auto">
      <div className="flex items-center justify-between mb-3">
        <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-zinc-400">
          <Clock className="h-4 w-4" />
          Diseños anteriores
        </h3>
        <Button variant="ghost" onClick={handleClear}>
          <Trash2 className="h-3.5 w-3.5" />
          Limpiar
        </Button>
      </div>

      <div className="space-y-1">
        {entries.map((entry) => (
          <button
            key={entry.session_id}
            onClick={() => handleSelect(entry.session_id)}
            className="w-full flex items-center justify-between rounded-lg px-3 py-2 text-left text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors"
          >
            <span className="truncate">{entry.label}</span>
            <span className="shrink-0 text-xs text-zinc-600">
              {new Date(entry.timestamp).toLocaleDateString('es')}
            </span>
          </button>
        ))}
      </div>
    </Card>
  );
}

// ─── Helper para agregar al historial ─────────────────────────────────────────

export function addToHistory(sessionId: string, label: string) {
  const entries = loadHistory();
  // Evitar duplicados
  const filtered = entries.filter((e) => e.session_id !== sessionId);
  filtered.unshift({ session_id: sessionId, label, timestamp: new Date().toISOString() });
  // Mantener máximo 20 entradas
  saveHistory(filtered.slice(0, 20));
}
