from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.p4_core import (
    EXPECTED_CLIENTS,
    EXPECTED_DATE,
    EXPECTED_ROUTE_ID,
    ZONE_IDS,
    authoritative_metadata,
    build_final_map,
    comparison_rows,
    explanation_ca,
    files_status,
    fmt,
    load_data,
    ordered_driver_rows,
    p4_paths,
    role_label_ca,
    route_metrics,
    route_stops,
    truck_layout_figure,
)


st.set_page_config(page_title="Damm Smart Planner", page_icon="🚚", layout="wide")

st.markdown(
    """
<style>
  .block-container { padding-top: 1.4rem; }
  .hero {
    border-left: 8px solid #b91c1c;
    background: linear-gradient(90deg, #fff7ed 0%, #ffffff 78%);
    padding: 1.3rem 1.5rem;
    border-radius: 8px;
    margin-bottom: 1.2rem;
  }
  .hero h1 { margin: 0; color: #991b1b; font-size: 2.5rem; }
  .hero p { margin: .35rem 0 0 0; color: #404040; font-size: 1.05rem; }
  .section-title {
    color: #27272a;
    border-bottom: 3px solid #f2b705;
    padding-bottom: .35rem;
    margin-top: 1.8rem;
    margin-bottom: .8rem;
  }
  .metric-card {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: .9rem;
    box-shadow: 0 1px 6px rgba(0,0,0,.04);
    min-height: 94px;
  }
  .metric-label { color: #6b7280; text-transform: uppercase; font-size: .74rem; letter-spacing: .04em; }
  .metric-value { color: #1f2937; font-size: 1.35rem; font-weight: 800; margin-top: .25rem; }
  .logic-card { background: #fafafa; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1rem; height: 100%; }
  .ok { color: #166534; font-weight: 700; }
  .warn { color: #b45309; font-weight: 700; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def cached_data() -> dict:
    data = load_data(ROOT)
    build_final_map(data, ROOT)
    return load_data(ROOT)


def metric_card(label: str, value: str) -> None:
    st.markdown(
        f"<div class='metric-card'><div class='metric-label'>{label}</div><div class='metric-value'>{value}</div></div>",
        unsafe_allow_html=True,
    )


def embed_html_file(path: Path, height: int = 520) -> None:
    if path.exists():
        components.html(path.read_text(encoding="utf-8"), height=height, scrolling=True)
    else:
        st.info(f"Falta el fitxer de mapa o layout: `{path}`")


def download_button(label: str, path: Path, mime: str) -> None:
    if path.exists():
        st.download_button(label, data=path.read_bytes(), file_name=path.name, mime=mime, use_container_width=True)
    else:
        st.caption(f"Falta: `{path}`")


data = cached_data()
paths = p4_paths(ROOT)
meta = authoritative_metadata(data)
metrics = route_metrics(data)
baseline = data.get("baseline") or {}
route = data.get("optimized_route") or {}
layout = data.get("layout") or {}

st.markdown(
    """
<div class="hero">
  <h1>Damm Smart Planner</h1>
  <p>Optimització de ruta i càrrega per a repartiments Damm més eficients.</p>
</div>
""",
    unsafe_allow_html=True,
)

if data["missing"]:
    st.warning("Falten alguns fitxers d'entrada. Copia'ls a les rutes de P4 indicades a continuació:")
    for missing in data["missing"]:
        st.code(missing)

st.markdown("<h2 class='section-title'>1 — Resum executiu</h2>", unsafe_allow_html=True)
summary_cards = [
    ("Ruta", meta.get("route_id")),
    ("Data", meta.get("date")),
    ("Transport ID", meta.get("transport_id")),
    ("Repartidor", meta.get("driver")),
    ("Clients", meta.get("n_clients")),
    ("Distància original", fmt(metrics.get("baseline_km"), " km", digits=2)),
    ("Distància optimitzada", fmt(metrics.get("optimized_km"), " km", digits=2)),
    ("Km estalviats", fmt(metrics.get("km_saved"), " km", digits=2)),
    ("Temps original total", fmt(metrics.get("baseline_time_h"), " h", digits=2)),
    ("Temps operatiu estimat", fmt(metrics.get("optimized_time_h"), " h", digits=2)),
    ("Comparativa de temps", "No comparable"),
    ("Palets totals", fmt(metrics.get("total_pallets"), "", digits=2)),
    ("Zones del camió", metrics.get("truck_zones")),
    ("Clients grans", metrics.get("large_clients")),
    ("Productes comuns", metrics.get("common_products")),
]
cols = st.columns(5)
for idx, (label, value) in enumerate(summary_cards):
    with cols[idx % 5]:
        metric_card(label, str(value if value is not None else "n/a"))

st.markdown("<h2 class='section-title'>2 — Comparativa de ruta</h2>", unsafe_allow_html=True)
c1, c2 = st.columns(2)
with c1:
    st.subheader("Ruta original")
    st.write(
        {
            "ruta": baseline.get("ruta"),
            "transport": baseline.get("transport_id"),
            "clients": baseline.get("n_clients"),
            "línies": baseline.get("n_linies_comanda"),
            "km carretera OSRM": baseline.get("km_carretera_osrm"),
            "temps total h": baseline.get("temps_total_h"),
        }
    )
with c2:
    st.subheader("Ruta optimitzada")
    st.write(
        {
            "ruta al fitxer P2": route.get("route_id"),
            "ruta mostrada": meta.get("route_id"),
            "distància km": route.get("total_distance_km"),
            "temps estimat min": route.get("estimated_time_min"),
            "parades ordenades": len(route_stops(route)),
            "solver": route.get("solver"),
        }
    )
st.dataframe(pd.DataFrame(comparison_rows(data)), use_container_width=True, hide_index=True)

st.markdown("<h2 class='section-title'>3 — Visualització del mapa</h2>", unsafe_allow_html=True)
tabs = st.tabs(["Comparativa final", "Mapa original", "Mapa optimitzat"])
with tabs[0]:
    embed_html_file(paths["final_map"], height=560)
with tabs[1]:
    embed_html_file(paths["baseline_map"], height=560)
with tabs[2]:
    if paths["optimized_map"].exists():
        embed_html_file(paths["optimized_map"], height=560)
    else:
        st.info(
            "El mapa de ruta optimitzada no és present a P4. Copia'l a "
            f"`{paths['optimized_map']}` si vols incrustar el mapa original de P2."
        )

st.markdown("<h2 class='section-title'>4 — Distribució del camió</h2>", unsafe_allow_html=True)
st.plotly_chart(truck_layout_figure(layout), use_container_width=True)
if layout:
    zone_rows = []
    for zone in layout.get("zones", []):
        used = zone.get("used_pallets", zone.get("estimated_pallet_usage", 0))
        cap = zone.get("capacity_pallets", 1.0)
        zone_rows.append(
            {
                "zona": zone.get("zone_id"),
                "ús": role_label_ca(zone.get("role", "")),
                "clients assignats": ", ".join(str(c.get("client_name", "")) for c in zone.get("assigned_clients", [])[:4]),
                "productes principals": ", ".join(str(p.get("product_id", "")) for p in zone.get("assigned_products", [])[:5]),
                "ocupació": f"{used:.2f} / {cap:.2f} palet",
                "explicació": explanation_ca(zone.get("explanation")),
            }
        )
    st.dataframe(pd.DataFrame(zone_rows), use_container_width=True, hide_index=True)
    # Overflow warning
    overflow = layout.get("overflow")
    if overflow:
        st.error(
            f"⚠️ **Excés de càrrega estimat: {overflow.get('overflow_pallets', 0):.2f} palets**\n\n"
            f"{overflow.get('overflow_reason', '')}\n\n"
            "Cal planificar segon viatge o vehicle amb més capacitat."
        )
    with st.expander("Fitxer HTML original de layout P3"):
        if paths["layout_html"].exists():
            st.caption("El dashboard mostra el layout directament amb Plotly en català. També pots descarregar l'HTML original generat per P3.")
            st.download_button(
                "Descarregar layout_camio.html",
                data=paths["layout_html"].read_bytes(),
                file_name=paths["layout_html"].name,
                mime="text/html",
                use_container_width=True,
            )
        else:
            st.info(f"Falta el fitxer `{paths['layout_html']}`")

st.markdown("<h2 class='section-title'>5 — Explicació de la lògica de càrrega</h2>", unsafe_allow_html=True)
logic_cols = st.columns(5)
logic = [
    ("Productes comuns", "Els productes necessaris per a més del 50% dels clients van a zones centrals compartides perquè el repartidor els pugui agafar sovint."),
    ("Clients grans", "Els clients amb més d'1 palet tenen espai dedicat o de fàcil accés perquè el volum de descàrrega és rellevant."),
    ("Accés per ruta", "Les primeres parades es carreguen en posicions més accessibles; les últimes poden quedar més endins."),
    ("Retornables", "Els retornables utilitzen espai dinàmic perquè el camió es va buidant a mesura que entrega."),
    ("Apilat", "Barrils i CO2 a baix, caixes de vidre a zona mitjana-baixa, begudes mitjanes al mig i productes lleugers a dalt."),
]
for col, (title, body) in zip(logic_cols, logic):
    with col:
        st.markdown(f"<div class='logic-card'><b>{title}</b><br>{body}</div>", unsafe_allow_html=True)

st.markdown("<h2 class='section-title'>6 — Validació de dades</h2>", unsafe_allow_html=True)
clients_geo = data.get("clients_geo")
file_status_df = pd.DataFrame(files_status(data))
file_status_df["estat"] = file_status_df["exists"].map(lambda x: "OK" if x else "Falta")
st.dataframe(file_status_df, use_container_width=True, hide_index=True)
quality = {
    "files_clients_geo": len(clients_geo) if clients_geo is not None else None,
    "depot_0_existeix": bool(clients_geo is not None and (clients_geo["client_id"].astype(str) == "0").any()),
    "ruta_correcta": meta.get("route_id") == EXPECTED_ROUTE_ID,
    "data_correcta": meta.get("date") == EXPECTED_DATE,
    "clients_esperats": meta.get("n_clients") == EXPECTED_CLIENTS,
    "layout_te_6_zones": len(layout.get("zones", [])) == 6,
    "zones_exactes": [z.get("zone_id") for z in layout.get("zones", [])] == ZONE_IDS,
}
st.json(quality)

st.markdown("<h2 class='section-title'>7 — Descàrregues</h2>", unsafe_allow_html=True)
dcols = st.columns(3)
downloads = [
    ("Descarregar baseline_real.json", paths["baseline"], "application/json"),
    ("Descarregar ruta_optimitzada.json", paths["optimized_route"], "application/json"),
    ("Descarregar layout_camio.json", paths["layout"], "application/json"),
    ("Descarregar layout_camio_resum.json", paths["layout_summary"], "application/json"),
    ("Descarregar informe final PDF", paths["report_pdf"], "application/pdf"),
    ("Descarregar pla de càrrega PDF", paths["driver_pdf"], "application/pdf"),
]
for idx, item in enumerate(downloads):
    with dcols[idx % 3]:
        download_button(*item)

st.caption("P4 només llegeix les dades processades de P1/P2/P3. Els reports i actius del dashboard es generen a outputs/ i reports/.")
