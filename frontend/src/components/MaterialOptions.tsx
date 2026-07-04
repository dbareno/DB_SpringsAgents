import { Star } from 'lucide-react';
import Card from '@/components/ui/Card';
import Badge from '@/components/ui/Badge';
import type { CommercialOption } from '@/services/types';

interface MaterialOptionsProps {
  options: CommercialOption[];
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-zinc-500">{label}</p>
      <p className="font-mono text-zinc-100">{value}</p>
    </div>
  );
}

export default function MaterialOptions({ options }: MaterialOptionsProps) {
  if (!options || options.length === 0) {
    return null;
  }

  const recommended = options.find((o) => o.is_recommended) ?? options[0];
  const alternatives = options
    .filter((o) => o.proposal_id !== recommended.proposal_id)
    .sort((a, b) => a.rank - b.rank);

  return (
    <Card title="Opciones de material">
      {/* Opción recomendada (diseño primario aprobado) */}
      <div className="rounded-lg border border-emerald-700/50 bg-emerald-900/20 p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Star className="h-4 w-4 text-emerald-400" />
            <span className="text-sm font-semibold text-zinc-100">
              {recommended.material.name}
            </span>
          </div>
          <Badge variant="success">Recomendada</Badge>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-5">
          <Stat label="Ranking" value={`#${recommended.rank}`} />
          <Stat label="Score" value={recommended.composite_score.toFixed(3)} />
          <Stat
            label="F.S. corte"
            value={
              recommended.compliance.safety_factor_shear != null
                ? recommended.compliance.safety_factor_shear.toFixed(2)
                : '—'
            }
          />
          <Stat label="Masa (kg)" value={recommended.wire_mass_kg.toFixed(4)} />
          <Stat
            label="Costo (USD)"
            value={`$${recommended.material_cost_usd.toFixed(4)}`}
          />
        </div>
      </div>

      {/* Alternativas viables */}
      {alternatives.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800">
                <th className="py-2 pr-2 text-left font-medium text-zinc-400 w-8">#</th>
                <th className="py-2 px-2 text-left font-medium text-zinc-400">Material</th>
                <th className="py-2 px-2 text-right font-medium text-zinc-400">Score</th>
                <th className="py-2 px-2 text-right font-medium text-zinc-400">F.S. corte</th>
                <th className="py-2 px-2 text-right font-medium text-zinc-400">Masa (kg)</th>
                <th className="py-2 pl-2 text-right font-medium text-zinc-400">Costo (USD)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {alternatives.map((o) => (
                <tr key={o.proposal_id} className="hover:bg-zinc-800/30">
                  <td className="py-2.5 pr-2 font-mono text-sm text-zinc-500">
                    {o.rank}
                  </td>
                  <td className="py-2.5 px-2 text-xs text-zinc-300">
                    {o.material.name}
                  </td>
                  <td className="py-2.5 px-2 text-right font-mono text-zinc-300">
                    {o.composite_score.toFixed(3)}
                  </td>
                  <td className="py-2.5 px-2 text-right font-mono text-zinc-300">
                    {o.compliance.safety_factor_shear != null
                      ? o.compliance.safety_factor_shear.toFixed(2)
                      : '—'}
                  </td>
                  <td className="py-2.5 px-2 text-right font-mono text-zinc-300">
                    {o.wire_mass_kg.toFixed(4)}
                  </td>
                  <td className="py-2.5 pl-2 text-right font-mono text-zinc-100">
                    ${o.material_cost_usd.toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
