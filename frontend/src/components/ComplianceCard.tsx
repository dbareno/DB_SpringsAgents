import { CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';
import Card from '@/components/ui/Card';
import Badge from '@/components/ui/Badge';
import type { Compliance } from '@/services/types';

interface ComplianceCardProps {
  compliance: Compliance;
}

function SafetyFactorRow({
  label,
  value,
  threshold = 1,
}: {
  label: string;
  value: number | null;
  threshold?: number;
}) {
  if (value === null) {
    return (
      <div className="flex items-center justify-between py-2 border-b border-zinc-800/50 last:border-0">
        <span className="text-sm text-zinc-400">{label}</span>
        <span className="text-sm text-zinc-600">N/A</span>
      </div>
    );
  }

  const isSafe = value >= threshold;
  return (
    <div className="flex items-center justify-between py-2 border-b border-zinc-800/50 last:border-0">
      <span className="text-sm text-zinc-300">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm text-zinc-100">{value.toFixed(2)}</span>
        {isSafe ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-400" />
        ) : (
          <XCircle className="h-4 w-4 text-red-400" />
        )}
      </div>
    </div>
  );
}

export default function ComplianceCard({ compliance }: ComplianceCardProps) {
  return (
    <Card title="Verificación de cumplimiento">
      <div className="flex items-center gap-2 mb-4">
        <Badge variant={compliance.approved ? 'success' : 'error'}>
          {compliance.approved ? 'APROBADO' : 'NO APROBADO'}
        </Badge>
        <span className="text-xs text-zinc-500">
          Estándar: {compliance.applicable_standard}
        </span>
      </div>

      <div className="mb-4">
        <SafetyFactorRow
          label="Factor de seguridad (corte)"
          value={compliance.safety_factor_shear}
        />
        <SafetyFactorRow
          label="Factor de seguridad (pandeo)"
          value={compliance.safety_factor_buckling}
        />
        <SafetyFactorRow
          label="Factor de seguridad (fatiga)"
          value={compliance.safety_factor_fatigue}
        />
      </div>

      {compliance.failure_modes.length > 0 && (
        <div className="mb-4">
          <h4 className="flex items-center gap-1.5 text-sm font-medium text-red-400 mb-2">
            <AlertTriangle className="h-4 w-4" />
            Modos de falla identificados
          </h4>
          <ul className="space-y-1">
            {compliance.failure_modes.map((mode, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-zinc-400">
                <span className="text-red-400 mt-px shrink-0">•</span>
                <span>{mode}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {compliance.redesign_directives.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-amber-400 mb-2">
            Directivas de rediseño
          </h4>
          <ul className="space-y-1">
            {compliance.redesign_directives.map((d, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-zinc-400">
                <span className="text-amber-400 mt-px shrink-0">→</span>
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
