"""
app/services/export_service.py
─────────────────────────────────────────────────────────────────────────────
Export service — generates PDF technical drawings and DXF CAD files
from a completed spring design session.
"""

from __future__ import annotations

import io
import logging

import ezdxf
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DesignProject

logger = logging.getLogger(__name__)

# ── A4 dimensions in points ─────────────────────────────────────────────────
A4_W, A4_H = A4
MARGIN = 20 * mm


async def _load_report(
    session_id: str, db: AsyncSession
) -> dict | None:
    """Load the report JSON from a completed DesignProject row."""
    result = await db.execute(
        select(DesignProject).where(
            DesignProject.session_id == session_id,
            DesignProject.status == "approved",
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        return None
    return project.final_report


# ─────────────────────────────────────────────────────────────────────────────
# PDF Export
# ─────────────────────────────────────────────────────────────────────────────


def _build_pdf(report: dict) -> bytes:
    """Generate a technical-drawing PDF from a design report dict."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="Plano Técnico — Resorte de Compresión",
        author="Spring Design Agent",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleES",
        parent=styles["Title"],
        fontSize=18,
        leading=22,
        spaceAfter=6,
        textColor=colors.HexColor("#1a1a2e"),
    )
    heading_style = ParagraphStyle(
        "HeadingES",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        spaceBefore=12,
        spaceAfter=4,
        textColor=colors.HexColor("#16213e"),
    )
    normal = styles["Normal"]

    story: list = []

    # ── Title block ─────────────────────────────────────────────────────
    story.append(Paragraph("Plano Técnico — Resorte de Compresión", title_style))
    story.append(Spacer(1, 4))

    summary = report.get("summary", {})
    story.append(
        Paragraph(
            f"<b>Tipo:</b> {summary.get('spring_type', 'N/A')} &nbsp;&nbsp;"
            f"<b>Material:</b> {summary.get('material', 'N/A')} &nbsp;&nbsp;"
            f"<b>Norma:</b> {summary.get('applicable_standard', 'N/A')}",
            normal,
        )
    )
    story.append(Spacer(1, 8))

    # ── Geometry table ──────────────────────────────────────────────────
    geo = report.get("geometry", {})
    geo_rows: list[list[str]] = [
        ["Parámetro", "Valor", "Unidad"],
        ["Diámetro del alambre (d)", _fmt(geo.get("wire_diameter_mm")), "mm"],
        ["Diámetro medio (D)", _fmt(geo.get("mean_coil_diameter_mm")), "mm"],
        ["Diámetro exterior (OD)", _fmt(geo.get("outer_diameter_mm")), "mm"],
        ["Diámetro interior (ID)", _fmt(geo.get("inner_diameter_mm")), "mm"],
        ["Espiras activas (n_a)", _fmt(geo.get("active_coils")), "—"],
        ["Espiras totales (n_t)", _fmt(geo.get("total_coils")), "—"],
        ["Longitud libre (L₀)", _fmt(geo.get("free_length_mm")), "mm"],
        ["Paso (p)", _fmt(geo.get("pitch_mm")), "mm"],
        ["Índice del resorte (C)", _fmt(geo.get("spring_index")), "—"],
        ["Constante elástica (k)", _fmt(geo.get("spring_rate_n_mm")), "N/mm"],
        ["Factor de Wahl (K_w)", _fmt(geo.get("wahl_factor")), "—"],
        ["Esbeltez (λ)", _fmt(geo.get("slenderness_ratio")), "—"],
    ]
    if geo.get("torsion_moment_n_mm") is not None:
        geo_rows.append(
            ["Momento torsor", _fmt(geo["torsion_moment_n_mm"]), "N·mm"]
        )
    if geo.get("angular_deflection_deg") is not None:
        geo_rows.append(
            ["Deflexión angular", _fmt(geo["angular_deflection_deg"]), "°"]
        )

    story.append(Paragraph("Geometría", heading_style))
    story.append(_table(geo_rows, col_widths=[120, 80, 60]))
    story.append(Spacer(1, 8))

    # ── Material table ───────────────────────────────────────────────────
    mat = report.get("material", {})
    mat_rows: list[list[str]] = [
        ["Propiedad", "Valor", "Unidad"],
        ["Nombre", mat.get("name", "N/A"), "—"],
        ["Módulo de corte (G)", _fmt(mat.get("shear_modulus_gpa")), "GPa"],
        ["Módulo elástico (E)", _fmt(mat.get("elastic_modulus_gpa")), "GPa"],
        ["Densidad (ρ)", _fmt(mat.get("density_kg_m3")), "kg/m³"],
        ["Límite elástico (Sy)", _fmt(mat.get("yield_strength_mpa")), "MPa"],
        ["Resistencia última (Sut)", _fmt(mat.get("ultimate_strength_mpa")), "MPa"],
        ["Temp. máxima", _fmt(mat.get("max_temp_c")), "°C"],
        ["Costo", _fmt(mat.get("cost_usd_per_kg")), "USD/kg"],
    ]
    story.append(Paragraph("Material", heading_style))
    story.append(_table(mat_rows, col_widths=[120, 80, 60]))
    story.append(Spacer(1, 8))

    # ── Compliance table ─────────────────────────────────────────────────
    comp = report.get("compliance", {})
    comp_rows: list[list[str]] = [
        ["Verificación", "Valor", "Estado"],
        [
            "Factor seguridad corte (Sf_s)",
            _fmt(comp.get("safety_factor_shear")),
            "✅" if (comp.get("safety_factor_shear") or 0) >= 1.3 else "❌",
        ],
        [
            "Factor seguridad pandeo (Sf_b)",
            _fmt(comp.get("safety_factor_buckling")),
            "✅" if (comp.get("safety_factor_buckling") or 0) >= 1.3 else "❌",
        ],
    ]
    if comp.get("safety_factor_fatigue") is not None:
        comp_rows.append([
            "Factor seguridad fatiga",
            _fmt(comp["safety_factor_fatigue"]),
            "✅" if comp["safety_factor_fatigue"] >= 1.0 else "❌",
        ])
    story.append(Paragraph("Cumplimiento Normativo", heading_style))
    story.append(_table(comp_rows, col_widths=[120, 80, 60]))
    story.append(Spacer(1, 8))

    # ── Commercial table ─────────────────────────────────────────────────
    comm = report.get("commercial", {})
    ranked = comm.get("ranked_proposals", [])
    if ranked:
        top = ranked[0]
        comm_rows: list[list[str]] = [
            ["Métrica", "Valor", ""],
            ["Score compuesto", f"{top.get('composite_score', 0):.4f}", ""],
            ["Masa de alambre", f"{top.get('wire_mass_kg', 0):.6f}", "kg"],
            ["Costo material", f"${top.get('material_cost_usd', 0):.4f}", "USD"],
            ["Vida estimada", f"{top.get('estimated_life_cycles', 0):,}", "ciclos"],
        ]
        if "manufacturing_usd" in top:
            comm_rows.insert(
                3,
                ["Costo manufactura", f"${top['manufacturing_usd']:.4f}", "USD"],
            )
            comm_rows.insert(
                4,
                ["Costo total", f"${top['total_cost_usd']:.4f}", "USD"],
            )
        story.append(Paragraph("Evaluación Comercial (Mejor Propuesta)", heading_style))
        story.append(_table(comm_rows, col_widths=[120, 80, 60]))
        story.append(Spacer(1, 8))

    # ── Footer ───────────────────────────────────────────────────────────
    gen = report.get("generated_at", "")
    try:
       from datetime import datetime
       dt = datetime.fromisoformat(gen)
       gen_fmt = dt.strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
       gen_fmt = gen
    story.append(
        Paragraph(
            f"Generado por Spring Design Agent el {gen_fmt}",
            ParagraphStyle(
                "Footer",
                parent=normal,
                fontSize=8,
                textColor=colors.HexColor("#888888"),
            ),
        )
    )

    doc.build(story)
    return buf.getvalue()


def _fmt(val: object, decimals: int = 2) -> str:
    """Format a numeric value for table display; return '—' for None."""
    if val is None:
        return "—"
    try:
        return f"{float(val):.{decimals}f}"
    except (ValueError, TypeError):
        return str(val)


def _table(rows: list[list[str]], col_widths: list[int]) -> Table:
    """Build a styled Table from header + data rows."""
    t = Table(rows, colWidths=col_widths)
    style_cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]
    t.setStyle(TableStyle(style_cmds))
    return t


async def export_pdf(session_id: str, db: AsyncSession) -> bytes | None:
    """Generate a PDF technical drawing for a completed design session."""
    report = await _load_report(session_id, db)
    if report is None:
        return None
    try:
        return _build_pdf(report)
    except Exception as exc:
        logger.exception("PDF generation failed for session %s", session_id)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# DXF Export
# ─────────────────────────────────────────────────────────────────────────────


def _build_dxf(report: dict) -> bytes:
    """Generate a DXF CAD drawing from a design report dict.

    Produces a side-view compression spring silhouette with dimensions:
    - Zigzag wire representation with closed/ground ends
    - Centerline, OD, free length, and wire diameter callouts
    - Title block with key parameters
    """
    import tempfile
    import os

    geo = report.get("geometry", {})
    d = geo.get("wire_diameter_mm", 1.0)
    Dm = geo.get("mean_coil_diameter_mm", 10.0)
    OD = geo.get("outer_diameter_mm", Dm + d)
    total_coils = geo.get("total_coils", 5.0)
    free_length = geo.get("free_length_mm", 30.0)
    pitch = free_length / total_coils if total_coils > 0 else free_length

    CLOSE_END = 1.5  # closed/ground coils at each end

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4  # millimeters
    msp = doc.modelspace()

    doc.layers.add("SPRING", color=ezdxf.colors.CYAN)
    doc.layers.add("DIM", color=ezdxf.colors.YELLOW)
    doc.layers.add("CENTER", color=ezdxf.colors.RED, linetype="CENTER2")
    doc.layers.add("BORDER", color=ezdxf.colors.WHITE)

    x0, y0 = 25.0, 25.0

    # ── Spring coil zigzag ───────────────────────────────────────────────
    for i in range(int(total_coils)):
        y_bot = y0 + i * pitch
        y_top = y_bot + pitch
        is_end = i < CLOSE_END or i >= total_coils - CLOSE_END

        if is_end:
            # Closed/ground end: stacked horizontal lines
            msp.add_line(
                (x0, y_bot + d / 2),
                (x0 + OD, y_bot + d / 2),
                dxfattribs={"layer": "SPRING"},
            )
            msp.add_line(
                (x0, y_bot + pitch - d / 2),
                (x0 + OD, y_bot + pitch - d / 2),
                dxfattribs={"layer": "SPRING"},
            )
        else:
            # Active coil: zigzag across the OD
            mid = (y_bot + y_top) / 2
            # Front half (right-going)
            msp.add_line(
                (x0, y_bot + d / 2),
                (x0 + OD, mid - d / 2),
                dxfattribs={"layer": "SPRING"},
            )
            # Back half (left-going)
            msp.add_line(
                (x0 + OD, mid + d / 2),
                (x0, y_top - d / 2),
                dxfattribs={"layer": "SPRING"},
            )

    # ── Centerline ───────────────────────────────────────────────────────
    cl_x = x0 + OD / 2
    msp.add_line(
        (cl_x, y0 - 5),
        (cl_x, y0 + free_length + 5),
        dxfattribs={"layer": "CENTER"},
    )

    # ── OD dimension (below spring) ──────────────────────────────────────
    od_y = y0 - 8
    msp.add_line(
        (x0, od_y), (x0 + OD, od_y), dxfattribs={"layer": "DIM"}
    )
    # Tick marks (simple vertical lines instead of arrows for CAD compat)
    for x_pos in (x0, x0 + OD):
        msp.add_line(
            (x_pos, od_y - 2), (x_pos, od_y + 2), dxfattribs={"layer": "DIM"}
        )
    msp.add_text(
        f"OD={OD:.1f}",
        height=2.5,
        dxfattribs={"layer": "DIM"},
        ).set_placement((cl_x - 5, od_y - 7))

    # ── Free-length dimension (left side) ────────────────────────────────
    dim_x = x0 - 12
    msp.add_line(
        (dim_x, y0), (dim_x, y0 + free_length), dxfattribs={"layer": "DIM"}
    )
    for y_pos in (y0, y0 + free_length):
        msp.add_line(
            (dim_x - 2, y_pos), (dim_x + 2, y_pos), dxfattribs={"layer": "DIM"}
        )
    msp.add_text(
        f"L0={free_length:.1f}",
        height=2.5,
        dxfattribs={"layer": "DIM"},
        ).set_placement((dim_x - 12, y0 + free_length / 2 - 1.5))

    # ── Title block ──────────────────────────────────────────────────────
    border_w = 200
    border_h = 10
    msp.add_lwpolyline(
        [
            (0, 0),
            (border_w, 0),
            (border_w, border_h),
            (0, border_h),
            (0, 0),
        ],
        dxfattribs={"layer": "BORDER"},
    )
    mat = report.get("material", {})
    material_name = mat.get("name", "N/A")
    title = (
        f"RESORTE COMPRESION | "
        f"d={d:.2f} OD={OD:.1f} L0={free_length:.1f} "
        f"n={total_coils:.1f} | {material_name}"
    )
    msp.add_text(
        title, height=2.5, dxfattribs={"layer": "BORDER"}
        ).set_placement((3, 1.5))

    # ── Save to bytes via temp file ──────────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp_name = tmp.name
    try:
        doc.saveas(tmp_name)
        with open(tmp_name, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass


async def export_dxf(session_id: str, db: AsyncSession) -> bytes | None:
    """Generate a DXF CAD file for a completed design session."""
    report = await _load_report(session_id, db)
    if report is None:
        return None
    try:
        return _build_dxf(report)
    except Exception as exc:
        logger.exception("DXF generation failed for session %s", session_id)
        raise
