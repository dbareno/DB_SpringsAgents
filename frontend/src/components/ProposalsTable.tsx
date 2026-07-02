import { Trophy } from 'lucide-react';
import Card from '@/components/ui/Card';
import Badge from '@/components/ui/Badge';
import type { Proposal } from '@/services/types';

interface ProposalsTableProps {
  proposals: Proposal[];
}

const rankColors: Record<number, string> = {
  1: 'text-amber-400',
  2: 'text-zinc-300',
  3: 'text-amber-700',
};

export default function ProposalsTable({ proposals }: ProposalsTableProps) {
  if (!proposals || proposals.length === 0) {
    return (
      <Card title="Propuestas comerciales">
        <p className="text-sm text-zinc-500">No hay propuestas disponibles.</p>
      </Card>
    );
  }

  const sorted = [...proposals].sort((a, b) => a.rank - b.rank);

  return (
    <Card title="Propuestas comerciales">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800">
              <th className="py-2 pr-2 text-left font-medium text-zinc-400 w-8">#</th>
              <th className="py-2 px-2 text-left font-medium text-zinc-400">Propuesta</th>
              <th className="py-2 px-2 text-right font-medium text-zinc-400">Score</th>
              <th className="py-2 px-2 text-right font-medium text-zinc-400">Masa (kg)</th>
              <th className="py-2 px-2 text-right font-medium text-zinc-400">Costo (USD)</th>
              <th className="py-2 pl-2 text-right font-medium text-zinc-400">Ciclos de vida</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {sorted.map((p) => (
              <tr key={p.proposal_id} className="hover:bg-zinc-800/30">
                <td className="py-2.5 pr-2">
                  {p.rank === 1 ? (
                    <Trophy className={`h-4 w-4 ${rankColors[p.rank] ?? 'text-zinc-500'}`} />
                  ) : (
                    <span className={`font-mono text-sm ${rankColors[p.rank] ?? 'text-zinc-500'}`}>
                      {p.rank}
                    </span>
                  )}
                </td>
                <td className="py-2.5 px-2 font-mono text-xs text-zinc-300">
                  {p.proposal_id.replace('prop_', 'Prop. ')}
                </td>
                <td className="py-2.5 px-2 text-right">
                  <Badge
                    variant={p.composite_score >= 80 ? 'success' : p.composite_score >= 50 ? 'warning' : 'error'}
                  >
                    {p.composite_score.toFixed(1)}
                  </Badge>
                </td>
                <td className="py-2.5 px-2 text-right font-mono text-zinc-300">
                  {p.wire_mass_kg.toFixed(4)}
                </td>
                <td className="py-2.5 px-2 text-right font-mono text-zinc-100">
                  ${p.material_cost_usd.toFixed(2)}
                </td>
                <td className="py-2.5 pl-2 text-right font-mono text-zinc-300">
                  {p.estimated_life_cycles.toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
