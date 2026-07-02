import { Rotate3d } from 'lucide-react';
import Badge from '@/components/ui/Badge';
import type { Summary } from '@/services/types';

interface SummaryHeaderProps {
  summary: Summary;
}

export default function SummaryHeader({ summary }: SummaryHeaderProps) {
  return (
    <div className="flex flex-wrap items-center gap-3 mb-6">
      <div className="flex items-center gap-2">
        <div className="rounded-lg bg-blue-900/40 p-2">
          <Rotate3d className="h-5 w-5 text-blue-400" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-zinc-100">
            {summary.spring_type}
          </h2>
          <p className="text-xs text-zinc-500">{summary.material}</p>
        </div>
      </div>

      <div className="flex items-center gap-2 ml-auto">
        <span className="text-xs text-zinc-500">
          {summary.applicable_standard}
        </span>
        <Badge variant={summary.approved ? 'success' : 'error'}>
          {summary.approved ? 'Aprobado' : 'No aprobado'}
        </Badge>
      </div>
    </div>
  );
}
