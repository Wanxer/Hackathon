from __future__ import annotations

import argparse
import html as html_lib
import json
from pathlib import Path
import re
import sys
from typing import Any

from jinja2 import Template


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.p4_core import (
    authoritative_metadata,
    build_final_map,
    comparison_rows,
    ensure_output_dirs,
    explanation_ca,
    fmt,
    load_data,
    ordered_driver_rows,
    p4_paths,
    role_label_ca,
    route_metrics,
)


DAMM_RED = "#b91c1c"
DAMM_GOLD = "#f2b705"
DAMM_DARK = "#2b2b2b"
LIGHT_BG = "#fff8e6"


def clean_pdf_text(text: Any) -> str:
    """Return readable PDF-safe text with no visible HTML markup."""
    if text is None:
        return ""
    value = str(text)
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"</?\s*(b|strong|i|em)\s*>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html_lib.unescape(value)
    value = value.replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def html_text(value: Any) -> str:
    return html_lib.escape(clean_pdf_text(value), quote=True)


def pdf_text(value: Any) -> str:
    return html_lib.escape(clean_pdf_text(value), quote=False)


def role_color(role: str) -> str:
    return {
        "common_shared": DAMM_RED,
        "dedicated_large_client": "#d97706",
        "client_specific": "#166534",
        "returnables_dynamic": "#6d28d9",
        "mixed": DAMM_DARK,
    }.get(str(role or ""), "#4b5563")


def zone_instruction(zone: dict[str, Any]) -> str:
    role = zone.get("role")
    if role == "common_shared":
        return "Zona compartida per productes comuns de molts clients."
    if role == "returnables_dynamic":
        return "Espai reservat per envasos i caixes retornables."
    if role in {"dedicated_large_client", "client_specific"}:
        return "Zona per clients concrets segons l'ordre de ruta."
    return "Mantenir accessible des de la lona lateral."


def zone_contents_list(zone: dict[str, Any], max_items: int = 3) -> list[str]:
    clients = [str(c.get("client_name", "")) for c in zone.get("assigned_clients", []) if c.get("client_name")]
    products = [
        str(p.get("product_name") or p.get("product_id", ""))
        for p in zone.get("assigned_products", [])
        if p.get("product_name") or p.get("product_id")
    ]
    items = clients[:max_items] if clients else products[:max_items]
    if not items and zone.get("role") == "returnables_dynamic":
        items = ["Espai dinàmic per retornables"]
    if not items:
        items = ["Sense producte fix precarregat"]
    return [clean_pdf_text(item) for item in items]


def prepared_zones(layout: dict[str, Any]) -> list[dict[str, Any]]:
    zones = []
    order = {"L1": 0, "L2": 1, "L3": 2, "R1": 3, "R2": 4, "R3": 5}
    for zone in sorted(layout.get("zones", []), key=lambda z: order.get(z.get("zone_id"), 99)):
        contents = zone_contents_list(zone, max_items=3)
        # Use new capacity-aware fields if present
        used = zone.get("used_pallets", zone.get("estimated_pallet_usage", 0))
        cap = zone.get("capacity_pallets", 1.0)
        usage_str = f"{fmt(used, '', digits=2)} / {fmt(cap, '', digits=2)} palet"
        zones.append(
            {
                **zone,
                "role_label": role_label_ca(str(zone.get("role", ""))),
                "color": role_color(str(zone.get("role", ""))),
                "usage": usage_str,
                "contents_list": contents,
                "contents": "; ".join(contents),
                "explanation": explanation_ca(zone.get("explanation")),
                "instruction": zone_instruction(zone),
            }
        )
    return zones


def overflow_warning_html(layout: dict[str, Any]) -> str:
    """Generate HTML warning if there is overflow."""
    overflow = layout.get("overflow")
    if not overflow:
        return ""
    pallets = overflow.get("overflow_pallets", 0)
    reason = overflow.get("overflow_reason", "")
    return (
        f'<div class="conclusion" style="background:#fff1f2;border-left:5px solid #b91c1c;'
        f'padding:16px;margin-top:14px;">'
        f'<strong>\u26a0\ufe0f Exc\u00e9s de c\u00e0rrega estimat: {pallets:.2f} palets</strong>'
        f'<p style="margin:6px 0">{reason}</p>'
        f'<p style="margin:6px 0">Cal planificar segon viatge o vehicle amb m\u00e9s capacitat.</p>'
        f'</div>'
    )


def kpi_cards(meta: dict[str, Any], metrics_raw: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"label": "Clients", "value": str(meta.get("n_clients", "n/d")), "class_name": "red"},
        {"label": "Distància original", "value": fmt(metrics_raw.get("baseline_km"), " km", digits=2), "class_name": ""},
        {"label": "Distància optimitzada", "value": fmt(metrics_raw.get("optimized_km"), " km", digits=2), "class_name": ""},
        {"label": "Estalvi", "value": fmt(metrics_raw.get("km_saved"), " km", digits=2), "class_name": "red"},
        {"label": "Reducció de distància", "value": fmt(metrics_raw.get("km_improvement_pct"), "%", digits=1), "class_name": "red"},
        {"label": "Zones del camió", "value": str(metrics_raw.get("truck_zones") or "n/d"), "class_name": ""},
    ]


REPORT_TEMPLATE = Template(
    """<!doctype html>
<html lang="ca">
<head>
  <meta charset="utf-8">
  <title>Damm Smart Planner - Informe executiu</title>
  <style>
    body { font-family: Inter, Arial, sans-serif; color: #242424; margin: 0; background: #f3f4f6; }
    .page { max-width: 1080px; margin: 0 auto; background: white; }
    .cover { background: #2b2b2b; color: white; padding: 42px 52px; border-top: 8px solid #b91c1c; }
    h1 { margin: 0; color: white; font-size: 38px; letter-spacing: .2px; }
    .tagline { color: #fef3c7; font-size: 18px; margin-top: 8px; }
    .meta { color: #e5e7eb; margin-top: 18px; font-weight: 700; }
    .content { padding: 36px 52px 48px 52px; }
    h2 { color: #2b2b2b; border-bottom: 3px solid #f2b705; padding-bottom: 7px; margin-top: 32px; }
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 18px; }
    .card { border-radius: 10px; padding: 16px; background: #fff8e6; border: 1px solid #f3d46b; }
    .card.red { background: #fff1f2; border-color: #fecdd3; }
    .label { font-size: 12px; text-transform: uppercase; color: #6b7280; font-weight: 800; }
    .value { font-size: 24px; font-weight: 900; color: #2b2b2b; margin-top: 4px; }
    table { border-collapse: collapse; width: 100%; margin-top: 14px; font-size: 13px; }
    th { background: #2b2b2b; color: white; text-align: left; padding: 10px; }
    td { border-bottom: 1px solid #e5e7eb; padding: 10px; vertical-align: top; }
    .note { background: #fef3c7; border-left: 5px solid #f2b705; padding: 14px; margin-top: 14px; font-weight: 600; }
    .truck { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }
    .zone { color: white; border-radius: 8px; padding: 12px; min-height: 122px; }
    .zone-code { font-size: 20px; font-weight: 900; }
    .zone-role { font-weight: 800; margin-top: 2px; }
    .zone-use { color: rgba(255,255,255,.92); margin-top: 4px; }
    .zone ul { margin: 8px 0 0 16px; padding: 0; }
    .zone li { margin: 2px 0; }
    .frontrear { text-align: center; font-weight: 900; color: #2b2b2b; margin: 12px 0; }
    ul li { margin-bottom: 7px; }
    .conclusion { background: #fff1f2; border-left: 5px solid #b91c1c; padding: 16px; margin-top: 16px; font-weight: 700; }
    .footer { margin-top: 36px; color: #6b7280; font-size: 12px; }
  </style>
</head>
<body>
<main class="page">
  <section class="cover">
    <h1>Damm Smart Planner</h1>
    <div class="tagline">Optimització de ruta i càrrega per a repartiments Damm més eficients</div>
    <div class="meta">Ruta {{ meta.route_id }} · {{ meta.date }} · Transport {{ meta.transport_id }} · Repartidor {{ meta.driver }}</div>
  </section>
  <section class="content">
    <div class="grid">
      {% for card in kpis %}
      <div class="card {{ card.class_name }}"><div class="label">{{ card.label }}</div><div class="value">{{ card.value }}</div></div>
      {% endfor %}
    </div>

    <h2>Comparativa de ruta</h2>
    <table>
      <tr><th>Mètrica</th><th>Ruta original</th><th>Ruta optimitzada</th><th>Diferència</th><th>Interpretació</th></tr>
      {% for row in comparison %}
        <tr><td>{{ row.metric }}</td><td>{{ row.baseline }}</td><td>{{ row.optimized }}</td><td>{{ row.difference }}</td><td>{{ row.interpretation }}</td></tr>
      {% endfor %}
    </table>
    <div class="note">El temps operatiu depèn dels supòsits de servei i espera. La mètrica comparable principal d'aquesta demo és la distància per carretera.</div>

    <h2>Distribució del camió</h2>
    <div class="note" style="font-size:13px;">1 posició = 1 palet · Capacitat total: 6 posicions de palet</div>
    <div class="frontrear">DAVANT / CABINA</div>
    <div class="truck">
      {% for zone in zones[:3] %}
        <div class="zone" style="background: {{ zone.color }};">
          <div class="zone-code">{{ zone.zone_id }}</div>
          <div class="zone-role">{{ zone.role_label }}</div>
          <div class="zone-use">Ocupació: {{ zone.usage }}</div>
          <ul>{% for item in zone.contents_list %}<li>{{ item }}</li>{% endfor %}</ul>
        </div>
      {% endfor %}
      {% for zone in zones[3:] %}
        <div class="zone" style="background: {{ zone.color }};">
          <div class="zone-code">{{ zone.zone_id }}</div>
          <div class="zone-role">{{ zone.role_label }}</div>
          <div class="zone-use">Ocupació: {{ zone.usage }}</div>
          <ul>{% for item in zone.contents_list %}<li>{{ item }}</li>{% endfor %}</ul>
        </div>
      {% endfor %}
    </div>
    <div class="frontrear">DARRERE</div>
    {{ overflow_html }}

    <h2>Lògica de càrrega</h2>
    <ul>
      <li>Els productes comuns a més del 50% dels clients es col·loquen en una zona central compartida.</li>
      <li>Els clients grans, amb més d'1 palet, tenen espai dedicat o més accessible.</li>
      <li>Les primeres parades han de quedar fàcils d'agafar des de la lona lateral.</li>
      <li>Els retornables es gestionen amb una zona dinàmica que es va omplint a mesura que es descarrega.</li>
      <li>Els productes pesants van a la part inferior del palet.</li>
    </ul>

    <h2>Conclusió</h2>
    <div class="conclusion">
      El pla optimitzat redueix la distància per carretera en {{ metrics.km_saved }} i manté els 24 clients reals de la ruta.
      A més, incorpora una distribució de càrrega explicable perquè el repartidor pugui descarregar amb més ordre i menys confusió.
    </div>
    <div class="footer">Sortides generades: dashboard, mapa comparatiu final, informe executiu i pla de càrrega per al repartidor.</div>
  </section>
</main>
</body>
</html>"""
)


DRIVER_TEMPLATE = Template(
    """<!doctype html>
<html lang="ca">
<head>
  <meta charset="utf-8">
  <title>Pla de càrrega i repartiment</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; color: #222; background: #f3f4f6; }
    .page { max-width: 1040px; margin: 0 auto; background: white; padding: 34px 46px; }
    h1 { color: #b91c1c; margin-bottom: 4px; font-size: 32px; }
    h2 { margin-top: 26px; border-bottom: 3px solid #f2b705; padding-bottom: 6px; }
    .meta { color: #555; margin-bottom: 18px; font-weight: 700; }
    table { border-collapse: collapse; width: 100%; font-size: 12px; }
    th { background: #2b2b2b; color: white; padding: 8px; text-align: left; }
    td { border-bottom: 1px solid #ddd; padding: 8px; vertical-align: top; }
    .truck { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
    .zone { color: white; border-radius: 8px; padding: 10px; min-height: 96px; }
    .zone-code { font-size: 18px; font-weight: 900; }
    .zone-role { font-weight: 800; }
    .zone ul { margin: 6px 0 0 16px; padding: 0; }
    .frontrear { text-align: center; font-weight: 900; margin: 10px 0; }
    .note { background: #fef3c7; border-left: 5px solid #f2b705; padding: 12px; margin: 14px 0; }
    .instructions li { margin-bottom: 5px; }
  </style>
</head>
<body>
<main class="page">
  <h1>Pla de càrrega i repartiment</h1>
  <div class="meta">Ruta {{ meta.route_id }} · {{ meta.date }} · Transport {{ meta.transport_id }} · Repartidor {{ meta.driver }}</div>

  <h2>Instruccions generals</h2>
  <div class="note">
    <ul class="instructions">
      <li>Obrir la lona lateral.</li>
      <li>Buscar la zona indicada per cada parada.</li>
      <li>Descarregar primer els productes visibles i accessibles.</li>
      <li>Mantenir els productes pesants a la part baixa del palet.</li>
      <li>Reservar R3 per als retornables.</li>
      <li>Els retornables es van col·locant a R3 a mesura que es buida el camió.</li>
    </ul>
  </div>

  <h2>Distribució del camió</h2>
  <div class="note" style="font-size:12px;">1 posició = 1 palet · Capacitat total: 6 posicions de palet</div>
  <div class="frontrear">DAVANT / CABINA</div>
  <div class="truck">
    {% for zone in zones %}
      <div class="zone" style="background: {{ zone.color }};">
        <div class="zone-code">{{ zone.zone_id }}</div>
        <div class="zone-role">{{ zone.role_label }}</div>
        <div style="color:rgba(255,255,255,.92);margin-top:4px;">Ocupació: {{ zone.usage }}</div>
        <ul>{% for item in zone.contents_list %}<li>{{ item }}</li>{% endfor %}</ul>
      </div>
    {% endfor %}
  </div>
  <div class="frontrear">DARRERE</div>
  {{ overflow_html }}

  <h2>Taula de zones</h2>
  <table>
    <tr><th>Zona</th><th>Ús</th><th>Contingut principal</th><th>Instrucció</th></tr>
    {% for zone in zones %}
      <tr><td>{{ zone.zone_id }}</td><td>{{ zone.role_label }}</td><td>{{ zone.contents }}</td><td>{{ zone.instruction }}</td></tr>
    {% endfor %}
  </table>

  <h2>Ordre de parades</h2>
  <table>
    <tr><th>#</th><th>Client</th><th>ID client</th><th>Entrega</th><th>Retornables</th><th>Zona</th><th>Instrucció</th></tr>
    {% for row in stops %}
      <tr>
        <td>{{ row.stop_number }}</td><td>{{ row.client_name }}</td><td>{{ row.client_id }}</td>
        <td>{{ row.delivery_units }}</td><td>{{ row.expected_return_units }}</td><td>{{ row.assigned_zone }}</td>
        <td>{{ row.unloading_notes }}</td>
      </tr>
    {% endfor %}
  </table>
</main>
</body>
</html>"""
)


def paragraph(text_value: Any, style):
    from reportlab.platypus import Paragraph

    return Paragraph(pdf_text(text_value), style)


def set_table_style(table, commands: list[tuple]) -> None:
    from reportlab.platypus import TableStyle

    table.setStyle(TableStyle(commands))


def make_kpi_table(kpis: list[dict[str, str]], styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table

    cells = []
    for card in kpis:
        cells.append([paragraph(str(card["label"]).upper(), styles["KpiLabel"]), paragraph(card["value"], styles["KpiValue"])])
    rows = [[cells[i] for i in range(0, 3)], [cells[i] for i in range(3, 6)]]
    table = Table(rows, colWidths=[160, 160, 160], rowHeights=[58, 58])
    set_table_style(
        table,
        [
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(LIGHT_BG)),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#f3d46b")),
            ("INNERGRID", (0, 0), (-1, -1), 8, colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ],
    )
    return table


def make_route_table(comparison: list[dict[str, str]], styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table

    rows = [["Mètrica", "Ruta original", "Ruta optimitzada", "Diferència", "Interpretació"]]
    rows += [[r["metric"], r["baseline"], r["optimized"], r["difference"], r["interpretation"]] for r in comparison]
    table = Table(
        [[paragraph(cell, styles["Small"]) for cell in row] for row in rows],
        colWidths=[90, 80, 80, 78, 180],
        repeatRows=1,
    )
    set_table_style(
        table,
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(DAMM_DARK)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#fff7ed")),
        ],
    )
    return table


def zone_cell(zone: dict[str, Any], styles) -> list:
    rows = [
        paragraph(zone["zone_id"], styles["ZoneCode"]),
        paragraph(zone["role_label"], styles["Zone"]),
        paragraph(zone["usage"], styles["Zone"]),
    ]
    rows.extend(paragraph(item, styles["ZoneSmall"]) for item in zone["contents_list"][:3])
    return rows


def make_truck_table(zones: list[dict[str, Any]], styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table

    rows = [
        [paragraph("DAVANT / CABINA", styles["Center"])] * 3,
        [zone_cell(z, styles) for z in zones[:3]],
        [zone_cell(z, styles) for z in zones[3:]],
        [paragraph("DARRERE", styles["Center"])] * 3,
    ]
    table = Table(rows, colWidths=[166, 166, 166], rowHeights=[24, 92, 92, 24])
    commands = [
        ("SPAN", (0, 0), (-1, 0)),
        ("SPAN", (0, 3), (-1, 3)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fef3c7")),
        ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#fef3c7")),
        ("GRID", (0, 0), (-1, -1), 1, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 1), (-1, 2), 7),
        ("RIGHTPADDING", (0, 1), (-1, 2), 7),
        ("TOPPADDING", (0, 1), (-1, 2), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 2), 6),
    ]
    for idx, zone in enumerate(zones[:3]):
        commands.append(("BACKGROUND", (idx, 1), (idx, 1), colors.HexColor(zone["color"])))
    for idx, zone in enumerate(zones[3:]):
        commands.append(("BACKGROUND", (idx, 2), (idx, 2), colors.HexColor(zone["color"])))
    set_table_style(table, commands)
    return table


def make_zone_table(zones: list[dict[str, Any]], styles):
    from reportlab.lib import colors
    from reportlab.platypus import Table

    rows = [["Zona", "Ús", "Contingut principal", "Instrucció"]]
    rows += [[z["zone_id"], z["role_label"], z["contents"], z["instruction"]] for z in zones]
    table = Table(
        [[paragraph(cell, styles["Small"]) for cell in row] for row in rows],
        colWidths=[44, 92, 150, 220],
        repeatRows=1,
    )
    set_table_style(
        table,
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(DAMM_DARK)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ],
    )
    return table


def report_styles():
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CoverTitle", parent=styles["Title"], fontSize=28, leading=32, textColor=DAMM_RED, spaceAfter=6))
    styles.add(ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=11, leading=15, textColor=DAMM_DARK))
    styles.add(ParagraphStyle("Section", parent=styles["Heading2"], fontSize=15, leading=19, textColor=DAMM_DARK, spaceBefore=14, spaceAfter=8))
    styles.add(ParagraphStyle("Small", parent=styles["BodyText"], fontSize=7.6, leading=9.5))
    styles.add(ParagraphStyle("Note", parent=styles["BodyText"], fontSize=9, leading=12, backColor="#fef3c7", borderColor=DAMM_GOLD, borderWidth=0.8, borderPadding=7))
    styles.add(ParagraphStyle("Conclusion", parent=styles["BodyText"], fontSize=10, leading=14, textColor=DAMM_DARK, backColor="#fff1f2", borderColor=DAMM_RED, borderWidth=0.8, borderPadding=8))
    styles.add(ParagraphStyle("Center", parent=styles["BodyText"], fontSize=9, leading=11, alignment=TA_CENTER))
    styles.add(ParagraphStyle("Zone", parent=styles["BodyText"], fontSize=7.5, leading=8.5, textColor="white", alignment=TA_CENTER))
    styles.add(ParagraphStyle("ZoneSmall", parent=styles["BodyText"], fontSize=6.7, leading=7.6, textColor="white", alignment=TA_CENTER))
    styles.add(ParagraphStyle("ZoneCode", parent=styles["BodyText"], fontSize=13, leading=15, textColor="white", alignment=TA_CENTER))
    styles.add(ParagraphStyle("KpiLabel", parent=styles["BodyText"], fontSize=7.2, leading=8.4, textColor="#6b7280"))
    styles.add(ParagraphStyle("KpiValue", parent=styles["BodyText"], fontSize=15, leading=17, textColor=DAMM_DARK))
    return styles


def build_executive_pdf(meta: dict[str, Any], metrics_raw: dict[str, Any], comparison: list[dict[str, str]], zones: list[dict[str, Any]], output: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = report_styles()
    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=38, leftMargin=38, topMargin=34, bottomMargin=32)
    story = [
        Paragraph(pdf_text("Damm Smart Planner"), styles["CoverTitle"]),
        paragraph("Optimització de ruta i càrrega per a repartiments Damm més eficients", styles["Subtitle"]),
        paragraph(f"Ruta {meta['route_id']} · {meta['date']} · Transport {meta['transport_id']} · Repartidor {meta['driver']}", styles["Subtitle"]),
        Spacer(1, 14),
        make_kpi_table(kpi_cards(meta, metrics_raw), styles),
        paragraph("Comparativa de ruta", styles["Section"]),
        make_route_table(comparison, styles),
        Spacer(1, 7),
        paragraph("El temps operatiu depèn dels supòsits de servei i espera. La mètrica comparable principal d'aquesta demo és la distància per carretera.", styles["Note"]),
        paragraph("Distribució del camió", styles["Section"]),
        make_truck_table(zones, styles),
        paragraph("Lògica de càrrega", styles["Section"]),
        paragraph(
            "Els productes comuns a més del 50% dels clients es col·loquen en una zona central compartida. "
            "Els clients grans, amb més d'1 palet, tenen espai dedicat o més accessible. "
            "Les primeres parades han de quedar fàcils d'agafar des de la lona lateral. "
            "Els retornables es gestionen amb una zona dinàmica que es va omplint a mesura que es descarrega. "
            "Els productes pesants van a la part inferior del palet.",
            styles["BodyText"],
        ),
        paragraph("Conclusió", styles["Section"]),
        paragraph(
            f"El pla optimitzat redueix la distància per carretera en {fmt(metrics_raw.get('km_saved'), ' km', digits=2)} "
            "i manté els 24 clients reals de la ruta. A més, incorpora una distribució de càrrega explicable "
            "perquè el repartidor pugui descarregar amb més ordre i menys confusió.",
            styles["Conclusion"],
        ),
    ]
    doc.build(story)


def build_driver_pdf(meta: dict[str, Any], zones: list[dict[str, Any]], stops: list[dict[str, Any]], output: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer, Table

    styles = report_styles()
    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    stop_rows = [["#", "Client", "ID client", "Entrega", "Retornables", "Zona", "Instrucció"]]
    stop_rows += [
        [
            row["stop_number"],
            row["client_name"],
            row["client_id"],
            row["delivery_units"],
            row["expected_return_units"],
            row["assigned_zone"],
            row["unloading_notes"],
        ]
        for row in stops
    ]
    stop_table = Table(
        [[paragraph(cell, styles["Small"]) for cell in row] for row in stop_rows],
        colWidths=[24, 122, 70, 48, 44, 50, 146],
        repeatRows=1,
    )
    set_table_style(
        stop_table,
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(DAMM_DARK)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ],
    )
    instructions = (
        "Obrir la lona lateral. Buscar la zona indicada per cada parada. Descarregar primer els productes visibles i accessibles. "
        "Mantenir els productes pesants a la part baixa del palet. Reservar R3 per als retornables. "
        "Els retornables es van col·locant a R3 a mesura que es buida el camió."
    )
    story = [
        paragraph("Pla de càrrega i repartiment", styles["CoverTitle"]),
        paragraph(f"Ruta {meta['route_id']} · {meta['date']} · Transport {meta['transport_id']} · Repartidor {meta['driver']}", styles["Subtitle"]),
        Spacer(1, 10),
        paragraph(instructions, styles["Note"]),
        paragraph("Distribució del camió", styles["Section"]),
        make_truck_table(zones, styles),
        paragraph("Taula de zones", styles["Section"]),
        make_zone_table(zones, styles),
        PageBreak(),
        paragraph("Ordre de parades", styles["Section"]),
        stop_table,
    ]
    doc.build(story)


def generate_reports(root: Path = ROOT) -> dict[str, str]:
    ensure_output_dirs(root)
    data = load_data(root)
    build_final_map(data, root)
    paths = p4_paths(root)
    meta = authoritative_metadata(data)
    metrics_raw = route_metrics(data)
    metrics = {
        "baseline_km": fmt(metrics_raw.get("baseline_km"), " km", digits=2),
        "optimized_km": fmt(metrics_raw.get("optimized_km"), " km", digits=2),
        "km_saved": fmt(metrics_raw.get("km_saved"), " km", digits=2),
        "km_improvement_pct": fmt(metrics_raw.get("km_improvement_pct"), "%", digits=1),
        "baseline_time_h": fmt(metrics_raw.get("baseline_time_h"), " h", digits=2),
        "optimized_time_h": fmt(metrics_raw.get("optimized_time_h"), " h", digits=2),
        "truck_zones": metrics_raw.get("truck_zones"),
    }
    comparison = comparison_rows(data)
    layout = data.get("layout") or {}
    zones = prepared_zones(layout)
    stops = ordered_driver_rows(data)
    outputs = [
        str(paths["report_html"]),
        str(paths["report_pdf"]),
        str(paths["driver_html"]),
        str(paths["driver_pdf"]),
        str(paths["final_map"]),
    ]

    overflow_html_str = overflow_warning_html(layout)

    report_html = REPORT_TEMPLATE.render(
        meta=meta,
        metrics=metrics,
        kpis=kpi_cards(meta, metrics_raw),
        comparison=comparison,
        zones=zones,
        outputs=outputs,
        overflow_html=overflow_html_str,
    )
    paths["report_html"].write_text(report_html, encoding="utf-8")

    driver_html = DRIVER_TEMPLATE.render(
        meta=meta, stops=stops, zones=zones,
        overflow_html=overflow_html_str,
    )
    paths["driver_html"].write_text(driver_html, encoding="utf-8")

    build_executive_pdf(meta, metrics_raw, comparison, zones, paths["report_pdf"])
    build_driver_pdf(meta, zones, stops, paths["driver_pdf"])

    return {key: str(paths[key]) for key in ["report_html", "report_pdf", "driver_html", "driver_pdf", "final_map"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera els reports finals de P4.")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    outputs = generate_reports(args.root.resolve())
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
