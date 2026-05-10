from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


EXPECTED_ROUTE_ID = "DR0051"
EXPECTED_DATE = "2026-02-05"
EXPECTED_TRANSPORT_ID = 11443257
EXPECTED_DRIVER = 855190
EXPECTED_CLIENTS = 24
ZONE_IDS = ["L1", "L2", "L3", "R1", "R2", "R3"]
ZONE_CAPACITY = 1.0  # Each zone is exactly 1 physical pallet slot
MAX_TOTAL_PALLETS = 6.0  # Truck has 6 physical pallet positions

ZONE_DEFINITIONS = [
    {"zone_id": "L1", "side": "left",  "position": "front",  "priority": 1},
    {"zone_id": "L2", "side": "left",  "position": "middle", "priority": 2},
    {"zone_id": "L3", "side": "left",  "position": "back",   "priority": 5},
    {"zone_id": "R1", "side": "right", "position": "front",  "priority": 3},
    {"zone_id": "R2", "side": "right", "position": "middle", "priority": 4},
    {"zone_id": "R3", "side": "right", "position": "back",   "priority": 6},
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def p4_paths(root: Path | None = None) -> dict[str, Path]:
    root = root or project_root()
    return {
        "clients_geo": root / "data" / "processed" / "clients_geo.csv",
        "cas_us_clients": root / "data" / "processed" / "cas_us_clients.csv",
        "cas_us_linies": root / "data" / "processed" / "cas_us_linies.csv",
        "baseline": root / "data" / "processed" / "baseline_real.json",
        "baseline_geometry": root / "data" / "processed" / "baseline_route_geometry.geojson",
        "optimized_route": root / "data" / "processed" / "ruta_optimitzada.json",
        "layout": root / "data" / "processed" / "layout_camio.json",
        "layout_summary": root / "data" / "processed" / "layout_camio_resum.json",
        "baseline_map": root / "outputs" / "mapa_baseline.html",
        "optimized_map": root / "outputs" / "ruta_optimitzada_map.html",
        "layout_html": root / "outputs" / "layout_camio.html",
        "final_map": root / "outputs" / "final_route_comparison_map.html",
        "report_html": root / "reports" / "damm_smart_planner_report.html",
        "report_pdf": root / "reports" / "damm_smart_planner_report.pdf",
        "driver_html": root / "reports" / "driver_loading_plan.html",
        "driver_pdf": root / "reports" / "driver_loading_plan.pdf",
    }


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_csv(path)


def load_data(root: Path | None = None) -> dict[str, Any]:
    paths = p4_paths(root)
    data: dict[str, Any] = {"paths": paths, "missing": []}
    for key in ["clients_geo", "cas_us_clients", "cas_us_linies"]:
        data[key] = read_csv(paths[key])
        if data[key] is None:
            data["missing"].append(str(paths[key]))
    for key in ["baseline", "optimized_route", "layout", "layout_summary", "baseline_geometry"]:
        data[key] = read_json(paths[key])
        if data[key] is None and key in {"baseline", "optimized_route", "layout"}:
            data["missing"].append(str(paths[key]))
    return data


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def safe_round(value: Any, digits: int = 2) -> float | None:
    value = safe_float(value)
    return round(value, digits) if value is not None else None


def fmt(value: Any, suffix: str = "", digits: int = 1, missing: str = "n/a") -> str:
    number = safe_float(value)
    if number is None:
        return missing
    if abs(number - int(number)) < 0.0001:
        return f"{int(number):,}{suffix}"
    english = f"{number:,.{digits}f}"
    catalan = english.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{catalan}{suffix}"


def route_stops(route: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not route:
        return []
    return [s for s in route.get("ordered_stops", []) if str(s.get("client_id")) not in {"0", "DEPOT"}]


def authoritative_metadata(data: dict[str, Any]) -> dict[str, Any]:
    baseline = data.get("baseline") or {}
    route = data.get("optimized_route") or {}
    layout = data.get("layout") or {}
    stops = route_stops(route)
    clients_geo = data.get("clients_geo")
    n_clients = baseline.get("n_clients")
    if n_clients is None and clients_geo is not None:
        n_clients = int((clients_geo["client_id"].astype(str) != "0").sum())
    if n_clients is None:
        n_clients = len(stops)
    return {
        "route_id": baseline.get("ruta") or layout.get("route_id") or route.get("route_id"),
        "date": baseline.get("data") or layout.get("date") or route.get("date"),
        "transport_id": baseline.get("transport_id") or layout.get("transport_id") or route.get("transport_id"),
        "driver": baseline.get("repartidor") or layout.get("driver") or route.get("driver"),
        "n_clients": n_clients,
        "depot": "DDI Mollet",
    }


def route_metrics(data: dict[str, Any]) -> dict[str, Any]:
    baseline = data.get("baseline") or {}
    route = data.get("optimized_route") or {}
    layout = data.get("layout") or {}
    baseline_km = safe_float(baseline.get("km_carretera_osrm"))
    optimized_km = safe_float(route.get("total_distance_km"))
    baseline_time_h = safe_float(baseline.get("temps_total_h"))
    optimized_min = safe_float(route.get("estimated_time_min"))
    optimized_time_h = optimized_min / 60 if optimized_min is not None else None
    km_saved = baseline_km - optimized_km if baseline_km is not None and optimized_km is not None else None
    return {
        "baseline_km": safe_round(baseline_km),
        "optimized_km": safe_round(optimized_km),
        "km_saved": safe_round(km_saved),
        "km_improvement_pct": safe_round((km_saved / baseline_km) * 100 if baseline_km and km_saved is not None else None),
        "baseline_time_h": safe_round(baseline_time_h),
        "baseline_driving_time_h": safe_round(baseline.get("temps_conduccio_h_osrm")),
        "optimized_time_h": safe_round(optimized_time_h),
        "optimized_time_label": "Estimació operativa",
        "time_saved_h": None,
        "time_improvement_pct": None,
        "time_comparison_note": "El temps operatiu depèn dels supòsits de servei i espera; la mètrica comparable principal és la distància per carretera.",
        "total_pallets": safe_round(baseline.get("palets_totals")),
        "truck_zones": len(layout.get("zones", [])) if layout else None,
        "large_clients": len(layout.get("large_clients", [])) if layout else None,
        "common_products": len(layout.get("common_products", [])) if layout else None,
    }


def comparison_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = route_metrics(data)
    meta = authoritative_metadata(data)
    return [
        {
            "metric": "Distància per carretera",
            "baseline": fmt(metrics["baseline_km"], " km", digits=2),
            "optimized": fmt(metrics["optimized_km"], " km", digits=2),
            "difference": f"{fmt(metrics['km_saved'], ' km', digits=2)} menys",
            "improvement_pct": fmt(metrics["km_improvement_pct"], "%", digits=1),
            "interpretation": "KPI principal: menys quilòmetres conduïts.",
        },
        {
            "metric": "Temps de conducció",
            "baseline": fmt(metrics["baseline_driving_time_h"], " h", digits=2),
            "optimized": "n/d",
            "difference": "No es declara estalvi",
            "improvement_pct": "n/d",
            "interpretation": "No hi ha temps de conducció optimitzat exportat de forma comparable.",
        },
        {
            "metric": "Temps operatiu estimat",
            "baseline": fmt(metrics["baseline_time_h"], " h", digits=2),
            "optimized": fmt(metrics["optimized_time_h"], " h", digits=2),
            "difference": "No comparable",
            "improvement_pct": "n/d",
            "interpretation": "Inclou supòsits de servei, espera o descàrrega; no és el KPI principal.",
        },
        {
            "metric": "Clients servits",
            "baseline": fmt(meta["n_clients"]),
            "optimized": fmt(meta["n_clients"]),
            "difference": "0",
            "improvement_pct": "mateixa ruta",
            "interpretation": "Mateixos clients reals en els dos escenaris.",
        },
    ]


def role_label_ca(role: str) -> str:
    return {
        "common_shared": "Productes comuns",
        "dedicated_large_client": "Client gran dedicat",
        "client_specific": "Clients específics",
        "returnables_dynamic": "Retornables",
        "mixed": "Mixta",
    }.get(str(role or ""), str(role or "").replace("_", " ").title())


def explanation_ca(text: Any) -> str:
    raw = str(text or "")
    translations = {
        "Central shared pallet for products used by more than half of route clients.": "Zona central compartida per productes comuns de molts clients.",
        "Dedicated/easy-access pallet for a client above 1 pallet equivalent.": "Zona dedicada o més accessible per a un client de volum alt.",
        "Mixed zone combining shared and client-specific products while keeping access explainable.": "Zona mixta amb productes compartits i de clients concrets, mantenint l'accés clar.",
        "Client-specific products placed by optimized stop order and side-curtain accessibility.": "Productes de clients específics col·locats segons l'ordre de ruta i l'accés per lona lateral.",
        "Dynamic empty space for returnables. It is not fully preloaded; space becomes available progressively as deliveries are unloaded and expected returns are collected.": "Espai dinàmic per retornables. No es precarrega; es va omplint a mesura que es descarrega el camió.",
    }
    return translations.get(raw, raw)


def role_color(role: str) -> str:
    return {
        "common_shared": "#b91c1c",
        "dedicated_large_client": "#d97706",
        "client_specific": "#166534",
        "returnables_dynamic": "#6d28d9",
        "mixed": "#374151",
    }.get(role, "#4b5563")


def truck_layout_figure(layout: dict[str, Any] | None):
    import plotly.graph_objects as go

    fig = go.Figure()
    if not layout:
        fig.update_layout(title="Distribució del camió no disponible")
        return fig
    coords = {"L1": (0, 2), "L2": (1, 2), "L3": (2, 2), "R1": (0, 0), "R2": (1, 0), "R3": (2, 0)}
    for zone in layout.get("zones", []):
        zid = zone.get("zone_id")
        x, y = coords.get(zid, (0, 0))
        clients = zone.get("assigned_clients", [])
        products = zone.get("assigned_products", [])
        client_text = "<br>".join(f"{c.get('stop_number', '')}. {c.get('client_name', '')}" for c in clients[:4])
        product_text = "<br>".join(f"{p.get('product_id')}: {p.get('product_name')}" for p in products[:5])
        # Use new capacity-aware fields if present
        used = zone.get("used_pallets", zone.get("estimated_pallet_usage", 0))
        cap = zone.get("capacity_pallets", ZONE_CAPACITY)
        usage_label = f"{used:.2f} / {cap:.2f} palet"
        hover = (
            f"<b>{zid}</b><br>Ús: {role_label_ca(zone.get('role', ''))}<br>"
            f"Ocupació: {usage_label}<br>"
            f"Pes estimat: {zone.get('estimated_weight_kg', 0)} kg<br><br>"
            f"<b>Clients</b><br>{client_text or 'Compartit/dinàmic'}<br><br>"
            f"<b>Productes</b><br>{product_text or 'Sense producte fix precarregat'}<br><br>"
            f"{explanation_ca(zone.get('explanation', ''))}"
        )
        label = (
            f"<b>{zid}</b><br>{role_label_ca(zone.get('role', ''))}<br>"
            f"{usage_label}"
        )
        fig.add_trace(
            go.Scatter(
                x=[x],
                y=[y],
                mode="markers+text",
                marker={"symbol": "square", "size": 135, "color": role_color(zone.get("role", "")), "line": {"color": "white", "width": 3}},
                text=[label],
                textposition="middle center",
                textfont={"color": "white", "size": 12},
                hovertext=[hover],
                hoverinfo="text",
                showlegend=False,
            )
        )
    # Overflow warning annotation
    overflow = layout.get("overflow")
    if overflow:
        fig.add_annotation(
            x=1, y=-0.6,
            text=f"⚠️ {overflow.get('overflow_reason', 'Excés de càrrega')}",
            showarrow=False,
            font={"size": 12, "color": "#b91c1c"},
            bgcolor="#fff1f2",
            bordercolor="#b91c1c",
            borderwidth=1,
            borderpad=6,
        )
    fig.add_annotation(x=-0.45, y=1, text="<b>DAVANT / CABINA</b>", showarrow=False, textangle=-90)
    fig.add_annotation(x=2.55, y=1, text="<b>DARRERE</b>", showarrow=False, textangle=90)
    fig.update_layout(
        height=520,
        margin={"l": 30, "r": 30, "t": 20, "b": 20},
        plot_bgcolor="#f7f7f7",
        paper_bgcolor="white",
        xaxis={"visible": False, "range": [-0.8, 2.8]},
        yaxis={"visible": False, "range": [-1, 3.2]},
    )
    return fig


def client_zone_lookup(layout: dict[str, Any] | None) -> dict[str, str]:
    lookup: dict[str, str] = {}
    if not layout:
        return lookup
    for zone in layout.get("zones", []):
        if zone.get("role") == "returnables_dynamic":
            continue
        for client in zone.get("assigned_clients", []):
            cid = str(client.get("client_id"))
            lookup.setdefault(cid, zone.get("zone_id", ""))
    return lookup


def ordered_driver_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    route = data.get("optimized_route") or {}
    layout = data.get("layout") or {}
    zone_map = client_zone_lookup(layout)
    rows = []
    for stop in sorted(route_stops(route), key=lambda s: int(s.get("stop_number") or 0)):
        cid = str(stop.get("client_id"))
        assigned_zone = zone_map.get(cid, "L2/R2 compartida")
        delivery_units = safe_float(stop.get("delivery_units"), 0) or 0
        return_units = safe_float(stop.get("expected_return_units"), 0) or 0
        if assigned_zone == "R3":
            note = "Deixar retornables a R3."
        elif return_units >= 15:
            note = "Recollida alta de retornables. Reservar espai a R3."
        elif delivery_units >= 60:
            note = "Parada amb volum alt. Preparar més temps de descàrrega."
        elif assigned_zone in {"L2", "R2", "L2/R2 compartida"}:
            note = "Zona central. Revisar productes compartits."
        else:
            note = "Obrir la lona lateral i descarregar des de la zona indicada."
        rows.append(
            {
                "stop_number": stop.get("stop_number"),
                "client_id": cid,
                "client_name": stop.get("client_name"),
                "delivery_units": stop.get("delivery_units"),
                "expected_return_units": stop.get("expected_return_units"),
                "assigned_zone": assigned_zone,
                "unloading_notes": note,
                "returnables_notes": "Col·locar els retornables a R3 a mesura que es buida el camió.",
            }
        )
    return rows


def ensure_output_dirs(root: Path | None = None) -> None:
    root = root or project_root()
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)


def build_final_map(data: dict[str, Any], root: Path | None = None) -> Path | None:
    root = root or project_root()
    ensure_output_dirs(root)
    paths = p4_paths(root)
    clients = data.get("clients_geo")
    route = data.get("optimized_route")
    baseline_geometry = data.get("baseline_geometry")
    if clients is None or route is None:
        return None
    try:
        import folium
    except Exception:
        return None
    m = folium.Map(location=[clients["lat"].mean(), clients["lon"].mean()], zoom_start=11, tiles="OpenStreetMap")
    if baseline_geometry:
        folium.GeoJson(
            baseline_geometry,
            name="Ruta original OSRM",
            style_function=lambda _: {"color": "#991b1b", "weight": 5, "opacity": 0.75},
        ).add_to(m)
    depot_rows = clients[clients["client_id"].astype(str) == "0"]
    depot = depot_rows.iloc[0] if not depot_rows.empty else None
    opt_coords: list[list[float]] = []
    if depot is not None:
        opt_coords.append([float(depot["lat"]), float(depot["lon"])])
    for stop in sorted(route_stops(route), key=lambda s: int(s.get("stop_number") or 0)):
        if stop.get("lat") is not None and stop.get("lon") is not None:
            opt_coords.append([float(stop["lat"]), float(stop["lon"])])
    if depot is not None:
        opt_coords.append([float(depot["lat"]), float(depot["lon"])])
    if len(opt_coords) > 2:
        folium.PolyLine(opt_coords, color="#f2b705", weight=4, opacity=0.9, tooltip="Ordre de ruta optimitzada").add_to(m)
    if depot is not None:
        folium.Marker(
            [depot["lat"], depot["lon"]],
            tooltip="Dipòsit - DDI Mollet",
            popup="<b>0. DDI Mollet</b>",
            icon=folium.Icon(color="black", icon="home", prefix="fa"),
        ).add_to(m)
    stop_lookup = {str(s.get("client_id")): s for s in route_stops(route)}
    for _, row in clients[clients["client_id"].astype(str) != "0"].iterrows():
        stop = stop_lookup.get(str(row["client_id"]), {})
        label = stop.get("stop_number", "")
        folium.Marker(
            [row["lat"], row["lon"]],
            tooltip=f"{label}. {row['nom']}",
            popup=f"<b>{label}. {row['nom']}</b><br>{row['poblacio']}",
            icon=folium.DivIcon(
                html=(
                    "<div style='background:#b91c1c;color:white;border-radius:14px;width:28px;height:28px;"
                    "line-height:28px;text-align:center;font-weight:700;border:2px solid white;'>"
                    f"{label}</div>"
                )
            ),
        ).add_to(m)
    folium.LayerControl().add_to(m)
    m.save(paths["final_map"])
    opt_map = folium.Map(location=[clients["lat"].mean(), clients["lon"].mean()], zoom_start=11, tiles="OpenStreetMap")
    if len(opt_coords) > 2:
        folium.PolyLine(opt_coords, color="#f2b705", weight=5, opacity=0.9, tooltip="Ruta optimitzada DR0051").add_to(opt_map)
    if depot is not None:
        folium.Marker(
            [depot["lat"], depot["lon"]],
            tooltip="Dipòsit - DDI Mollet",
            popup="<b>0. DDI Mollet</b>",
            icon=folium.Icon(color="black", icon="home", prefix="fa"),
        ).add_to(opt_map)
    for _, row in clients[clients["client_id"].astype(str) != "0"].iterrows():
        stop = stop_lookup.get(str(row["client_id"]), {})
        label = stop.get("stop_number", "")
        folium.Marker(
            [row["lat"], row["lon"]],
            tooltip=f"{label}. {row['nom']}",
            popup=f"<b>{label}. {row['nom']}</b><br>{row['poblacio']}",
            icon=folium.DivIcon(
                html=(
                    "<div style='background:#f2b705;color:#1f2937;border-radius:14px;width:28px;height:28px;"
                    "line-height:28px;text-align:center;font-weight:800;border:2px solid white;'>"
                    f"{label}</div>"
                )
            ),
        ).add_to(opt_map)
    opt_map.save(paths["optimized_map"])
    return paths["final_map"]


def files_status(data: dict[str, Any]) -> list[dict[str, Any]]:
    paths = data["paths"]
    keys = [
        "clients_geo",
        "cas_us_clients",
        "cas_us_linies",
        "baseline",
        "baseline_geometry",
        "optimized_route",
        "layout",
        "layout_summary",
        "baseline_map",
        "optimized_map",
        "layout_html",
    ]
    return [{"file": str(paths[key]), "exists": paths[key].exists()} for key in keys]


def build_physical_layout(raw_layout: dict[str, Any]) -> dict[str, Any]:
    """
    Convert the raw P3/P4 layout (which may have >1.00 pallet per zone)
    into a physically coherent layout where each zone is exactly 1 pallet slot.

    Strategy:
      1. Collect all products from all zones with their pallet_equivalent values
      2. Separate by role: returnables, common/shared, client-specific
      3. Assign to 6 physical zones, each capped at 1.00 pallet
      4. When a group exceeds 1.00, split across multiple zones
      5. Report overflow if total demand > 6.00
    """
    # ── Gather all products and clients from the raw layout ──
    all_products: list[dict[str, Any]] = []
    all_clients: list[dict[str, Any]] = []
    returnable_products: list[dict[str, Any]] = []
    common_products: list[dict[str, Any]] = []
    client_specific_products: list[dict[str, Any]] = []

    for zone in raw_layout.get("zones", []):
        role = zone.get("role", "mixed")
        zone_clients = zone.get("assigned_clients", [])
        zone_products = zone.get("assigned_products", [])

        for client in zone_clients:
            client_copy = dict(client)
            client_copy["_source_zone"] = zone.get("zone_id")
            client_copy["_role"] = role
            all_clients.append(client_copy)

        for product in zone_products:
            product_copy = dict(product)
            product_copy["_source_zone"] = zone.get("zone_id")
            product_copy["_role"] = role

            if role == "returnables_dynamic":
                returnable_products.append(product_copy)
            elif role == "common_shared":
                common_products.append(product_copy)
            elif product_copy.get("is_returnable_line"):
                returnable_products.append(product_copy)
            elif product_copy.get("allocation_rule") == "shared_common_products":
                common_products.append(product_copy)
            else:
                client_specific_products.append(product_copy)

    # Calculate total demand
    total_returnable = sum(p.get("pallet_equivalent", 0) for p in returnable_products)
    total_common = sum(p.get("pallet_equivalent", 0) for p in common_products)
    total_client_specific = sum(p.get("pallet_equivalent", 0) for p in client_specific_products)
    total_demand = total_returnable + total_common + total_client_specific

    # ── Initialize 6 physical zones ──
    zones: dict[str, dict[str, Any]] = {}
    for zdef in ZONE_DEFINITIONS:
        zid = zdef["zone_id"]
        zones[zid] = {
            "zone_id": zid,
            "side": zdef["side"],
            "position": zdef["position"],
            "role": "mixed",
            "capacity_pallets": ZONE_CAPACITY,
            "used_pallets": 0.0,
            "free_pallets": ZONE_CAPACITY,
            "assigned_clients": [],
            "assigned_products": [],
            "estimated_weight_kg": 0.0,
            "explanation": "",
        }

    def zone_free(zid: str) -> float:
        return zones[zid]["free_pallets"]

    def add_product_to_zone(zid: str, product: dict[str, Any], pallet_amount: float) -> None:
        """Add a product (or fraction of it) to a zone."""
        amount = min(pallet_amount, zone_free(zid))
        if amount <= 0.0001:
            return
        p = dict(product)
        p["pallet_equivalent"] = round(amount, 4)
        # Scale weight proportionally if splitting
        orig_pe = product.get("pallet_equivalent", 0)
        if orig_pe > 0:
            ratio = amount / orig_pe
            p["estimated_weight_kg"] = round(product.get("estimated_weight_kg", 0) * ratio, 1)
        zones[zid]["assigned_products"].append(p)
        zones[zid]["used_pallets"] = round(zones[zid]["used_pallets"] + amount, 4)
        zones[zid]["free_pallets"] = round(ZONE_CAPACITY - zones[zid]["used_pallets"], 4)
        zones[zid]["estimated_weight_kg"] = round(
            zones[zid]["estimated_weight_kg"] + p.get("estimated_weight_kg", 0), 1
        )

    def assign_products_to_zones(
        products: list[dict[str, Any]],
        preferred_zones: list[str],
        fallback_zones: list[str],
    ) -> list[dict[str, Any]]:
        """
        Assign products to zones respecting capacity.
        Returns list of overflow products that didn't fit.
        """
        overflow: list[dict[str, Any]] = []
        for product in products:
            remaining = product.get("pallet_equivalent", 0)
            if remaining <= 0.0001:
                continue
            placed = False
            for zid in preferred_zones + fallback_zones:
                free = zone_free(zid)
                if free <= 0.0001:
                    continue
                amount = min(remaining, free)
                add_product_to_zone(zid, product, amount)
                remaining = round(remaining - amount, 4)
                placed = True
                if remaining <= 0.0001:
                    break
            if remaining > 0.0001:
                overflow_p = dict(product)
                overflow_p["pallet_equivalent"] = round(remaining, 4)
                overflow.append(overflow_p)
        return overflow

    # ── Step 1: Assign returnables to R3 ──
    zones["R3"]["role"] = "returnables_dynamic"
    zones["R3"]["explanation"] = (
        "Espai dinàmic per retornables. No es precarrega; es va omplint "
        "a mesura que es descarrega el camió."
    )
    ret_overflow = assign_products_to_zones(
        returnable_products, ["R3"], []
    )
    # Assign returnable clients to R3
    ret_client_ids = set()
    for p in returnable_products:
        src = p.get("_source_zone", "")
        for z in raw_layout.get("zones", []):
            if z.get("zone_id") == src and z.get("role") == "returnables_dynamic":
                for c in z.get("assigned_clients", []):
                    cid = str(c.get("client_id", ""))
                    if cid not in ret_client_ids:
                        ret_client_ids.add(cid)
                        zones["R3"]["assigned_clients"].append(c)

    # ── Step 2: Assign common/shared products to L2, R2 ──
    zones["L2"]["role"] = "common_shared"
    zones["L2"]["explanation"] = (
        "Zona central compartida per productes comuns de molts clients. "
        "1 posició = 1 palet."
    )
    zones["R2"]["role"] = "common_shared"
    zones["R2"]["explanation"] = (
        "Zona central compartida per productes comuns de molts clients. "
        "1 posició = 1 palet."
    )
    common_overflow = assign_products_to_zones(
        common_products, ["L2", "R2"], ["L3", "R1", "L1"]
    )

    # ── Step 3: Assign client-specific products to remaining zones ──
    # Group by source zone to maintain locality, assign in route order
    # Preferred order: L1, R1, L3, then overflow into any free zone
    client_zone_order = ["L1", "R1", "L3", "L2", "R2"]
    all_zone_order = ["L1", "R1", "L3", "L2", "R2", "R3"]

    # Group client-specific products by client for better assignment
    client_products: dict[str, list[dict[str, Any]]] = {}
    for p in client_specific_products:
        src_zone = p.get("_source_zone", "unknown")
        client_products.setdefault(src_zone, []).append(p)

    specific_overflow: list[dict[str, Any]] = []
    for src_zone_id, products in client_products.items():
        overflow = assign_products_to_zones(
            products, client_zone_order, all_zone_order
        )
        specific_overflow.extend(overflow)

    # Assign client-specific clients to zones where their products landed
    # Map: client_id -> set of zones where they have products
    client_zone_map: dict[str, set[str]] = {}
    for zid, zone_data in zones.items():
        for p in zone_data["assigned_products"]:
            src = p.get("_source_zone", "")
            # Find clients from the original zone
            for raw_zone in raw_layout.get("zones", []):
                if raw_zone.get("zone_id") == src:
                    for c in raw_zone.get("assigned_clients", []):
                        cid = str(c.get("client_id", ""))
                        client_zone_map.setdefault(cid, set()).add(zid)

    # Place each client in the zone where most of their products are
    placed_clients: set[str] = set(ret_client_ids)
    for raw_zone in raw_layout.get("zones", []):
        if raw_zone.get("role") == "returnables_dynamic":
            continue
        for c in raw_zone.get("assigned_clients", []):
            cid = str(c.get("client_id", ""))
            if cid in placed_clients:
                continue
            placed_clients.add(cid)
            target_zones = client_zone_map.get(cid, set())
            if target_zones:
                # Pick the zone that has the most products for this client
                best_zone = next(iter(sorted(target_zones)))
                zones[best_zone]["assigned_clients"].append(c)
            else:
                # Fallback: place in first zone with space
                for zid in client_zone_order:
                    if zone_free(zid) >= 0:
                        zones[zid]["assigned_clients"].append(c)
                        break

    # Set roles for mixed zones
    for zid in ["L1", "R1", "L3"]:
        if zones[zid]["role"] == "mixed":
            if zones[zid]["assigned_clients"]:
                zones[zid]["role"] = "client_specific"
                zones[zid]["explanation"] = (
                    "Productes de clients específics col·locats segons l'ordre de ruta "
                    "i l'accés per lona lateral. 1 posició = 1 palet."
                )
            elif zones[zid]["assigned_products"]:
                zones[zid]["role"] = "mixed"
                zones[zid]["explanation"] = (
                    "Zona mixta amb productes compartits i de clients concrets, "
                    "mantenint l'accés clar. 1 posició = 1 palet."
                )
            else:
                zones[zid]["explanation"] = "Zona buida disponible. 1 posició = 1 palet."

    # ── Build overflow report ──
    all_overflow = ret_overflow + common_overflow + specific_overflow
    overflow_total = round(sum(p.get("pallet_equivalent", 0) for p in all_overflow), 2)
    overflow_data = None

    if overflow_total > 0.001 or total_demand > MAX_TOTAL_PALLETS + 0.001:
        overflow_clients_set: set[str] = set()
        overflow_product_ids: list[str] = []
        for p in all_overflow:
            pid = p.get("product_id", "unknown")
            if pid not in overflow_product_ids:
                overflow_product_ids.append(pid)
        overflow_data = {
            "overflow_pallets": round(max(overflow_total, total_demand - MAX_TOTAL_PALLETS), 2),
            "overflow_products": overflow_product_ids[:20],
            "overflow_reason": (
                f"La càrrega estimada ({total_demand:.2f} palets) supera la capacitat "
                f"física de {int(MAX_TOTAL_PALLETS)} palets. "
                "Cal segon viatge, camió més gran o reduir càrrega."
            ),
            "total_demand_pallets": round(total_demand, 2),
        }

    # ── Assemble final layout ──
    ordered_zones = [zones[zid] for zid in ZONE_IDS]

    # Clean internal keys from products
    for zone_data in ordered_zones:
        for p in zone_data["assigned_products"]:
            p.pop("_source_zone", None)
            p.pop("_role", None)
        for c in zone_data["assigned_clients"]:
            c.pop("_source_zone", None)
            c.pop("_role", None)

    result: dict[str, Any] = {
        "route_id": raw_layout.get("route_id"),
        "date": raw_layout.get("date"),
        "transport_id": raw_layout.get("transport_id"),
        "driver": raw_layout.get("driver"),
        "truck_model": {
            "model": "6_pallet_zone_side_curtain",
            "zones": ZONE_IDS,
            "description": (
                "Sis posicions de palet: L1-L3 esquerra, R1-R3 dreta. "
                "Accés per lona lateral. 1 posició = 1 palet."
            ),
            "central_shared_positions": ["L2", "R2"],
            "returnables_dynamic_position": "R3",
            "capacity_per_zone": ZONE_CAPACITY,
            "total_capacity": MAX_TOTAL_PALLETS,
        },
        "zones": ordered_zones,
        "summary": {
            "total_used_pallets": round(sum(z["used_pallets"] for z in ordered_zones), 2),
            "total_capacity_pallets": MAX_TOTAL_PALLETS,
            "total_estimated_weight_kg": round(
                sum(z["estimated_weight_kg"] for z in ordered_zones), 1
            ),
            "total_demand_pallets": round(total_demand, 2),
            "has_overflow": overflow_data is not None,
        },
    }
    if overflow_data:
        result["overflow"] = overflow_data

    return result


def build_layout_summary(layout: dict[str, Any]) -> dict[str, Any]:
    """Build the layout_camio_resum.json from a physical layout."""
    summary = layout.get("summary", {})
    n_clients = sum(
        len(z.get("assigned_clients", []))
        for z in layout.get("zones", [])
    )
    return {
        "route_id": layout.get("route_id"),
        "date": layout.get("date"),
        "number_of_clients": n_clients,
        "total_used_pallets": summary.get("total_used_pallets", 0),
        "total_capacity_pallets": summary.get("total_capacity_pallets", MAX_TOTAL_PALLETS),
        "total_demand_pallets": summary.get("total_demand_pallets", 0),
        "total_estimated_weight_kg": summary.get("total_estimated_weight_kg", 0),
        "has_overflow": summary.get("has_overflow", False),
    }


def generate_layout_html(layout: dict[str, Any], output_path: Path) -> None:
    """Generate the layout_camio.html using Plotly."""
    fig = truck_layout_figure(layout)
    html_content = (
        '<html><head><meta charset="utf-8" /></head><body>'
        + fig.to_html(full_html=False, include_plotlyjs="cdn")
        + '</body></html>'
    )
    output_path.write_text(html_content, encoding="utf-8")
