'use client';

import dynamic from 'next/dynamic';
import { useState } from 'react';
import { Ruler, ShieldCheck, BarChart3, FileDown, FileOutput } from 'lucide-react';
import clsx from 'clsx';
import SummaryHeader from '@/components/SummaryHeader';
import GeometryTable from '@/components/GeometryTable';
import ComplianceCard from '@/components/ComplianceCard';
import ScoreChart from '@/components/ScoreChart';
import ProposalsTable from '@/components/ProposalsTable';
import MaterialOptions from '@/components/MaterialOptions';
import type { Report } from '@/services/types';

// ─── Three.js viewer con dynamic import (ssr: false) ─────────────────────────

const Spring3DViewer = dynamic(
  () => import('@/components/Spring3DViewer'),
  { ssr: false }
);

type TabKey = 'geometry' | 'compliance' | 'commercial';

interface TabDef {
  key: TabKey;
  label: string;
  icon: React.ReactNode;
}

const tabs: TabDef[] = [
  { key: 'geometry', label: 'Geometría', icon: <Ruler className="h-4 w-4" /> },
  { key: 'compliance', label: 'Cumplimiento', icon: <ShieldCheck className="h-4 w-4" /> },
  { key: 'commercial', label: 'Comercial', icon: <BarChart3 className="h-4 w-4" /> },
];

interface DesignResultProps {
  report: Report;
  sessionId: string;
  onExportPdf: () => void;
  onExportDxf: () => void;
}

export default function DesignResult({ report, sessionId, onExportPdf, onExportDxf }: DesignResultProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('geometry');

  return (
    <div className="w-full max-w-6xl mx-auto space-y-6">
      {/* Encabezado con resumen + export buttons */}
      <div className="flex items-start justify-between gap-4">
        <SummaryHeader summary={report.summary} />
        <div className="flex gap-2 shrink-0">
          <button
            onClick={onExportPdf}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-800 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-700 hover:text-zinc-100 transition-colors"
            title="Descargar plano PDF"
          >
            <FileDown className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">PDF</span>
          </button>
          <button
            onClick={onExportDxf}
            className="flex items-center gap-1.5 rounded-lg bg-zinc-800 px-3 py-2 text-xs font-medium text-zinc-300 hover:bg-zinc-700 hover:text-zinc-100 transition-colors"
            title="Descargar DXF para CAD"
          >
            <FileOutput className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">DXF</span>
          </button>
        </div>
      </div>

      {/* Grid principal: 3D viewer a la izquierda, datos a la derecha */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Visor 3D */}
        <Spring3DViewer scene={report.three_js_scene} />

        {/* Pestañas de datos */}
        <div className="flex flex-col gap-4">
          {/* Navegación de pestañas */}
          <div className="flex gap-1 rounded-lg bg-zinc-900 p-1">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={clsx(
                  'flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors flex-1 justify-center',
                  activeTab === tab.key
                    ? 'bg-zinc-800 text-zinc-100 shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-300'
                )}
              >
                {tab.icon}
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            ))}
          </div>

          {/* Contenido de la pestaña activa */}
          <div className="flex-1">
            {activeTab === 'geometry' && (
              <GeometryTable geometry={report.geometry} />
            )}
            {activeTab === 'compliance' && (
              <ComplianceCard compliance={report.compliance} />
            )}
            {activeTab === 'commercial' && (
              <div className="space-y-4">
                {/* Opciones de material (reportes antiguos no traen options) */}
                {report.commercial.options && report.commercial.options.length > 0 && (
                  <MaterialOptions options={report.commercial.options} />
                )}
                <ScoreChart data={report.commercial.chart_data} />
                <ProposalsTable proposals={report.commercial.ranked_proposals} />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Metadatos */}
      <div className="text-xs text-zinc-600 text-right">
        Generado: {new Date(report.generated_at).toLocaleString('es')}
      </div>
    </div>
  );
}
