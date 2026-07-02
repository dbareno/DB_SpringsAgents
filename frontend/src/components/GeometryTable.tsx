import { Ruler } from 'lucide-react';
import Card from '@/components/ui/Card';
import type { Geometry } from '@/services/types';

interface GeometryTableProps {
  geometry: Geometry;
}

const rows: Array<{ label: string; key: keyof Geometry; unit: string; decimals?: number }> = [
  { label: 'Diámetro del alambre (d)', key: 'wire_diameter_mm', unit: 'mm', decimals: 3 },
  { label: 'Diámetro medio de espira (D)', key: 'mean_coil_diameter_mm', unit: 'mm', decimals: 2 },
  { label: 'Diámetro exterior (De)', key: 'outer_diameter_mm', unit: 'mm', decimals: 2 },
  { label: 'Diámetro interior (Di)', key: 'inner_diameter_mm', unit: 'mm', decimals: 2 },
  { label: 'Espiras activas (Na)', key: 'active_coils', unit: '', decimals: 1 },
  { label: 'Espiras totales (Nt)', key: 'total_coils', unit: '', decimals: 1 },
  { label: 'Longitud libre (Lf)', key: 'free_length_mm', unit: 'mm', decimals: 2 },
  { label: 'Paso (p)', key: 'pitch_mm', unit: 'mm', decimals: 2 },
  { label: 'Índice del resorte (C)', key: 'spring_index', unit: '', decimals: 2 },
  { label: 'Rigidez (k)', key: 'spring_rate_n_mm', unit: 'N/mm', decimals: 3 },
  { label: 'Factor de Wahl (Kw)', key: 'wahl_factor', unit: '', decimals: 4 },
  { label: 'Tensión corregida (τ)', key: 'corrected_shear_stress_mpa', unit: 'MPa', decimals: 2 },
  { label: 'Relación de esbeltez (λ)', key: 'slenderness_ratio', unit: '', decimals: 2 },
];

function formatValue(geometry: Geometry, key: keyof Geometry, decimals?: number): string {
  const value = geometry[key];
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'Sí' : 'No';
  if (typeof value === 'number') {
    if (decimals !== undefined) return value.toFixed(decimals);
    return value.toString();
  }
  return String(value);
}

export default function GeometryTable({ geometry }: GeometryTableProps) {
  return (
    <Card title="Geometría del resorte">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800">
              <th className="py-2 pr-4 text-left font-medium text-zinc-400">Parámetro</th>
              <th className="py-2 text-right font-medium text-zinc-400">Valor</th>
              <th className="py-2 pl-4 text-right font-medium text-zinc-400">Unidad</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {rows.map((row) => (
              <tr key={row.key} className="hover:bg-zinc-800/30">
                <td className="py-2 pr-4 text-zinc-300">{row.label}</td>
                <td className="py-2 text-right font-mono text-zinc-100">
                  {formatValue(geometry, row.key, row.decimals)}
                </td>
                <td className="py-2 pl-4 text-right font-mono text-zinc-500">
                  {row.unit || '—'}
                </td>
              </tr>
            ))}
            {geometry.torsion_moment_n_mm !== null && (
              <tr className="hover:bg-zinc-800/30">
                <td className="py-2 pr-4 text-zinc-300">Momento torsor (Mt)</td>
                <td className="py-2 text-right font-mono text-zinc-100">
                  {geometry.torsion_moment_n_mm.toFixed(2)}
                </td>
                <td className="py-2 pl-4 text-right font-mono text-zinc-500">N·mm</td>
              </tr>
            )}
            {geometry.angular_deflection_deg !== null && (
              <tr className="hover:bg-zinc-800/30">
                <td className="py-2 pr-4 text-zinc-300">Deflexión angular</td>
                <td className="py-2 text-right font-mono text-zinc-100">
                  {geometry.angular_deflection_deg.toFixed(2)}
                </td>
                <td className="py-2 pl-4 text-right font-mono text-zinc-500">°</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
