'use client';

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import Card from '@/components/ui/Card';
import type { ChartData } from '@/services/types';

interface ScoreChartProps {
  data: ChartData[];
}

const CustomTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value: number; payload: ChartData }>;
  label?: string;
}) => {
  if (!active || !payload || !payload[0]) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-lg border border-zinc-700 bg-[#161b22] p-3 shadow-xl text-sm">
      <p className="font-medium text-zinc-200 mb-1">
        Propuesta {d.proposal_id.replace('prop_', '#')}
      </p>
      <div className="space-y-0.5 text-zinc-400">
        <p>Score: <span className="text-zinc-100 font-mono">{d.composite_score.toFixed(1)}</span></p>
        <p>Costo: <span className="text-zinc-100 font-mono">${d.material_cost_usd.toFixed(2)}</span></p>
        <p>Ciclos: <span className="text-zinc-100 font-mono">{d.estimated_life_cycles.toLocaleString()}</span></p>
        <p>FS corte: <span className="text-zinc-100 font-mono">{d.safety_factor_shear.toFixed(2)}</span></p>
        <p>FS pandeo: <span className="text-zinc-100 font-mono">{d.safety_factor_buckling.toFixed(2)}</span></p>
      </div>
    </div>
  );
};

export default function ScoreChart({ data }: ScoreChartProps) {
  const chartData = data.map((d) => ({
    name: d.proposal_id.replace('prop_', '#'),
    score: d.composite_score,
    ...d,
  }));

  return (
    <Card title="Score por propuesta">
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} barCategoryGap="20%">
            <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
            <XAxis
              dataKey="name"
              tick={{ fill: '#a1a1aa', fontSize: 12 }}
              axisLine={{ stroke: '#27272a' }}
            />
            <YAxis
              tick={{ fill: '#a1a1aa', fontSize: 12 }}
              axisLine={{ stroke: '#27272a' }}
              domain={[0, 100]}
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar
              dataKey="score"
              fill="#3b82f6"
              radius={[4, 4, 0, 0]}
              maxBarSize={48}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
