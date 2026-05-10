from __future__ import annotations

import argparse
import json
import math
import os
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd


TRANSPORT_ID = 11443257
ROUTE_ID = "DR0051"
ROUTE_DATE = "2026-02-05"
SOURCE_DATE = "05/02/2026"
DRIVER_ID = 855190
EXPECTED_CLIENTS = 24
EXPECTED_LINES = 196
DEPOT = {
    "client_id": 0,
    "nom": "DDI Mollet",
    "poblacio": "Mollet del Vallès",
    "lat": 41.5444,
    "lon": 2.2105,
    "palets": 0.0,
    "finestra_inici": "00:00:00",
    "finestra_fi": "23:59:59",
    "geo_status": "DEPOT_MANUAL",
    "ZonaTransp": "DEPOT",
    "zona_nom": "DEPOT",
}

FAKE_CLIENT_NAMES = {
    "RESTAURANT EL ROSER",
    "CELLER CALLDETENES",
    "SUKIPA",
    "BAR KARNAK",
    "L'ESPAI RESTAURANT",
    "LA COCA DE FOLGUEROLES",
    "CAL CISTELLER",
    "BAR PAVELLO ST JULIA VILATORTA",
}

# Manual coordinates are only fallbacks for real clients from cas_us_clients.csv.
# They are used when Nominatim is unavailable, rate-limited, or returns a point
# outside the expected Vic/Gurb area.
MANUAL_COORDS = {
    136675: (41.939354, 2.254115),
    9100058446: (41.934250, 2.194306),
    9100058476: (41.933278, 2.193306),
    9100058727: (41.930408, 2.253817),
    9100058850: (41.927242, 2.255694),
    9100134828: (41.929408, 2.253559),
    9100158925: (41.920984, 2.251159),
    9100324575: (41.925199, 2.253796),
    9100374304: (41.921100, 2.249800),
    9100374429: (41.929996, 2.255261),
    9100389125: (41.924275, 2.260045),
    9100397795: (41.939926, 2.238415),
    9100517701: (41.937500, 2.246000),
    9100564797: (41.930045, 2.253202),
    9100579824: (41.932992, 2.261336),
    9100610240: (41.935702, 2.259921),
    9100684868: (41.942746, 2.251518),
    9100700207: (41.931187, 2.254244),
    9100727009: (41.930535, 2.255095),
    9100740503: (41.920584, 2.250764),
    9100745910: (41.921807, 2.256738),
    9100746500: (41.929322, 2.256373),
    9100752860: (41.924354, 2.250692),
    9100759429: (41.973811, 2.271635),
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def processed_dir(root: Path) -> Path:
    path = root / "data" / "processed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def outputs_dir(root: Path) -> Path:
    path = root / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_client_id(value: Any) -> int:
    if pd.isna(value):
        raise ValueError("Missing client_id")
    return int(str(value).strip().split(".")[0])


def format_time(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = pd.to_datetime(text).time()
        return parsed.strftime("%H:%M:%S")
    except Exception:
        return text


def normalise_date(value: Any) -> str:
    parsed = pd.to_datetime(value, dayfirst=True, errors="raise")
    return parsed.strftime("%Y-%m-%d")


def palets_eq(row: pd.Series) -> float:
    qty = float(row["Cantidad entrega"])
    unit = str(row["Un.medida venta"]).strip()
    cpp = row.get("caixes_per_palet")
    if pd.isna(cpp) or float(cpp) <= 0:
        return 0.1
    cpp = float(cpp)
    if unit == "CAJ":
        return qty / cpp
    if unit == "BRL":
        return qty / 36
    if unit in {"BOT", "UN"}:
        return qty / (cpp * 6)
    return 0.1


def extract_case(root: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    root = root or repo_root()
    raw = root / "data" / "raw"
    processed = processed_dir(root)

    detail = pd.read_excel(raw / "Hackaton.xlsx", sheet_name="Detalle entrega")
    route = detail[detail["Transporte"] == TRANSPORT_ID].copy()
    if route.empty:
        raise ValueError(f"No rows found for transport {TRANSPORT_ID}")

    route["client_id"] = route["Destinatario mcía..1"].map(clean_client_id)
    route_date = normalise_date(route["FECHA"].iloc[0])
    assert len(route) == EXPECTED_LINES, f"Expected {EXPECTED_LINES} lines, got {len(route)}"
    assert route["Ruta"].nunique() == 1 and route["Ruta"].iloc[0] == ROUTE_ID
    assert route_date == ROUTE_DATE
    assert route["Repartidor"].nunique() == 1 and int(route["Repartidor"].iloc[0]) == DRIVER_ID
    assert route["client_id"].nunique() == EXPECTED_CLIENTS

    zm040 = pd.read_excel(raw / "ZM040.XLSX")
    palets_info = (
        zm040[zm040["UMA"] == "PAL"]
        .groupby("Material", as_index=False)
        .agg(
            caixes_per_palet=("Contador", "first"),
            volum_palet_l=("Volumen", "first"),
            pes_palet_kg=("Peso bruto", "first"),
        )
    )
    route_pal = route.merge(palets_info, on="Material", how="left")
    route_pal["palets_equiv"] = route_pal.apply(palets_eq, axis=1)

    palets_per_client = (
        route_pal.groupby("client_id", as_index=False)["palets_equiv"]
        .sum()
        .rename(columns={"palets_equiv": "palets"})
    )
    palets_per_client["palets"] = palets_per_client["palets"].round(2)

    clients = (
        route_pal.sort_values("Entrega")
        .groupby("client_id", as_index=False)
        .agg(
            nom=("Nombre 1", "first"),
            carrer=("Calle", "first"),
            cp=("CP", "first"),
            poblacio=("Población", "first"),
            ZonaTransp=("ZonaTransp", "first"),
            zona_nom=("ZonaTransp.1", "first"),
            n_linies=("Material", "count"),
            first_entrega=("Entrega", "min"),
        )
        .merge(palets_per_client, on="client_id", how="left")
        .sort_values("first_entrega")
    )

    horarios_path = raw / "Horarios_Entrega.XLSX"
    horaris_select = pd.DataFrame(columns=["client_id", "finestra_inici", "finestra_fi"])
    if horarios_path.exists():
        horarios = pd.read_excel(horarios_path)
        horaris_dia = horarios[(horarios["Día semana"] == 4) & (horarios["Turno"] == 1)].copy()
        horaris_dia["client_id"] = ("91" + horaris_dia["Deudor"].astype(str).str.zfill(8)).astype(int)
        horaris_select = (
            horaris_dia[horaris_dia["client_id"].isin(clients["client_id"])]
            [["client_id", "Horario inicia a", "Horario termina a"]]
            .drop_duplicates("client_id")
            .rename(
                columns={
                    "Horario inicia a": "finestra_inici",
                    "Horario termina a": "finestra_fi",
                }
            )
        )

    clients = clients.merge(horaris_select, on="client_id", how="left")
    clients["finestra_inici"] = clients["finestra_inici"].apply(format_time).replace("", "08:00:00")
    clients["finestra_fi"] = clients["finestra_fi"].apply(format_time).replace("", "18:00:00")
    clients["cp"] = clients["cp"].map(lambda x: str(int(x)).zfill(5) if pd.notna(x) else "")
    clients["adreca_completa"] = (
        clients["carrer"].astype(str)
        + ", "
        + clients["cp"].astype(str)
        + " "
        + clients["poblacio"].astype(str)
        + ", Barcelona, Catalunya, Spain"
    )

    client_columns = [
        "client_id",
        "nom",
        "carrer",
        "cp",
        "poblacio",
        "ZonaTransp",
        "zona_nom",
        "adreca_completa",
        "palets",
        "finestra_inici",
        "finestra_fi",
        "n_linies",
    ]
    clients = clients[client_columns]
    route_pal = route_pal.drop(columns=["client_id"])

    route_pal.to_csv(processed / "cas_us_linies.csv", index=False)
    clients.to_csv(processed / "cas_us_clients.csv", index=False)
    validate_extraction(root)
    return route_pal, clients


def validate_extraction(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    processed = processed_dir(root)
    lines = pd.read_csv(processed / "cas_us_linies.csv")
    clients = pd.read_csv(processed / "cas_us_clients.csv")
    checks = {
        "cas_us_linies_rows": len(lines),
        "cas_us_clients_rows": len(clients),
        "transport_ids": sorted(lines["Transporte"].dropna().unique().tolist()),
        "routes": sorted(lines["Ruta"].dropna().unique().tolist()),
        "dates": sorted(lines["FECHA"].dropna().unique().tolist()),
        "repartidors": sorted(lines["Repartidor"].dropna().unique().tolist()),
        "unique_clients_in_lines": lines["Destinatario mcía..1"].nunique(),
    }
    assert checks["cas_us_linies_rows"] == EXPECTED_LINES
    assert checks["cas_us_clients_rows"] == EXPECTED_CLIENTS
    assert checks["transport_ids"] == [TRANSPORT_ID]
    assert checks["routes"] == [ROUTE_ID]
    assert checks["repartidors"] == [DRIVER_ID]
    assert checks["unique_clients_in_lines"] == EXPECTED_CLIENTS
    assert all(normalise_date(d) == ROUTE_DATE for d in checks["dates"])
    return checks


def valid_osona_coordinate(lat: float, lon: float) -> bool:
    return 41.85 <= lat <= 42.02 and 2.15 <= lon <= 2.35


def build_geocode_query(row: pd.Series) -> str:
    return ", ".join(
        [
            str(row["carrer"]),
            str(row["cp"]),
            str(row["poblacio"]),
            "Barcelona",
            "Catalunya",
            "Spain",
        ]
    )


def try_nominatim(clients: pd.DataFrame) -> dict[int, tuple[float, float]]:
    try:
        import certifi
        from geopy.extra.rate_limiter import RateLimiter
        from geopy.geocoders import Nominatim
    except Exception as exc:
        print(f"Warning: geopy/certifi unavailable, using manual reviewed fallbacks ({exc}).")
        return {}

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    geolocator = Nominatim(user_agent="damm-smarttruck-p1-geocoder")
    geocode = RateLimiter(
        geolocator.geocode,
        min_delay_seconds=1.2,
        max_retries=1,
        error_wait_seconds=3,
        swallow_exceptions=True,
    )
    found: dict[int, tuple[float, float]] = {}
    for _, row in clients.iterrows():
        client_id = clean_client_id(row["client_id"])
        location = geocode(build_geocode_query(row), timeout=10, addressdetails=False)
        if location and valid_osona_coordinate(float(location.latitude), float(location.longitude)):
            found[client_id] = (float(location.latitude), float(location.longitude))
        else:
            print(f"Manual fallback needed for {client_id} - {row['nom']}")
    return found


def geocode_clients(root: Path | None = None, use_nominatim: bool = True) -> pd.DataFrame:
    root = root or repo_root()
    processed = processed_dir(root)
    clients = pd.read_csv(processed / "cas_us_clients.csv")
    assert len(clients) == EXPECTED_CLIENTS, f"Expected {EXPECTED_CLIENTS} real clients"

    nominatim_coords = try_nominatim(clients) if use_nominatim else {}
    rows: list[dict[str, Any]] = [DEPOT.copy()]

    for _, row in clients.iterrows():
        client_id = clean_client_id(row["client_id"])
        if client_id in nominatim_coords:
            lat, lon = nominatim_coords[client_id]
            status = "NOMINATIM"
        else:
            if client_id not in MANUAL_COORDS:
                raise ValueError(f"No geocoding fallback for real client {client_id} - {row['nom']}")
            lat, lon = MANUAL_COORDS[client_id]
            status = "MANUAL_REVIEWED"
        rows.append(
            {
                "client_id": client_id,
                "nom": row["nom"],
                "poblacio": row["poblacio"],
                "lat": lat,
                "lon": lon,
                "palets": float(row["palets"]),
                "finestra_inici": row["finestra_inici"],
                "finestra_fi": row["finestra_fi"],
                "geo_status": status,
                "ZonaTransp": row.get("ZonaTransp", ""),
                "zona_nom": row.get("zona_nom", ""),
            }
        )

    geo = pd.DataFrame(rows)
    columns = [
        "client_id",
        "nom",
        "poblacio",
        "lat",
        "lon",
        "palets",
        "finestra_inici",
        "finestra_fi",
        "geo_status",
        "ZonaTransp",
        "zona_nom",
    ]
    geo = geo[columns]
    geo.to_csv(processed / "clients_geo.csv", index=False)
    validate_geocoding(root)
    return geo


def validate_geocoding(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    processed = processed_dir(root)
    clients = pd.read_csv(processed / "cas_us_clients.csv")
    geo = pd.read_csv(processed / "clients_geo.csv")
    source_ids = {clean_client_id(v) for v in clients["client_id"]}
    geo_ids = [clean_client_id(v) for v in geo["client_id"]]
    non_depot_ids = {v for v in geo_ids if v != 0}
    geo_names = {str(v).strip().upper() for v in geo["nom"]}
    checks = {
        "clients_geo_rows": len(geo),
        "real_clients": len(non_depot_ids),
        "unique_client_ids": len(geo_ids) == len(set(geo_ids)),
        "first_row_is_depot": geo_ids[0] == 0,
        "missing_lat_lon": int(geo[["lat", "lon"]].isna().sum().sum()),
        "all_geo_clients_in_source": non_depot_ids == source_ids,
        "fake_clients_present": sorted(FAKE_CLIENT_NAMES.intersection(geo_names)),
    }
    assert checks["clients_geo_rows"] == EXPECTED_CLIENTS + 1
    assert checks["real_clients"] == EXPECTED_CLIENTS
    assert checks["unique_client_ids"]
    assert checks["first_row_is_depot"]
    assert checks["missing_lat_lon"] == 0
    assert checks["all_geo_clients_in_source"]
    assert not checks["fake_clients_present"]
    return checks


def infer_original_order(root: Path | None = None) -> pd.DataFrame:
    root = root or repo_root()
    processed = processed_dir(root)
    lines = pd.read_csv(processed / "cas_us_linies.csv")
    geo = pd.read_csv(processed / "clients_geo.csv")

    client_col = "Destinatario mcía..1"
    if client_col not in lines.columns:
        raise ValueError(f"Missing required client column: {client_col}")

    order = (
        lines.assign(client_id=lines[client_col].map(clean_client_id))
        .sort_values("Entrega")
        .drop_duplicates("client_id", keep="first")
        [["Entrega", "client_id", "Nombre 1"]]
    )
    assert len(order) == EXPECTED_CLIENTS

    merged = order.merge(geo, on="client_id", how="left", validate="one_to_one")
    if merged[["lat", "lon"]].isna().any().any():
        missing = merged[merged["lat"].isna() | merged["lon"].isna()]["client_id"].tolist()
        raise ValueError(f"Missing coordinates for clients in original order: {missing}")
    return merged


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fallback_geometry(points: pd.DataFrame) -> dict[str, Any]:
    return {
        "type": "LineString",
        "coordinates": [[float(row["lon"]), float(row["lat"])] for _, row in points.iterrows()],
    }


def fallback_distance_duration(points: pd.DataFrame) -> tuple[float, float]:
    km = 0.0
    for i in range(len(points) - 1):
        a = points.iloc[i]
        b = points.iloc[i + 1]
        km += haversine_km(float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"])) * 1.3
    hours = km / 40.0
    return km, hours


def request_osrm_route(points: pd.DataFrame) -> tuple[float, float, dict[str, Any], str]:
    import requests

    try:
        import certifi

        verify = certifi.where()
    except Exception:
        verify = True

    coords = ";".join(f"{row['lon']},{row['lat']}" for _, row in points.iterrows())
    url = f"https://router.project-osrm.org/route/v1/driving/{coords}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}
    response = requests.get(url, params=params, timeout=30, verify=verify)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError(f"Invalid OSRM response: {payload.get('code')}")
    route = payload["routes"][0]
    return route["distance"] / 1000, route["duration"] / 3600, route["geometry"], "osrm"


def build_baseline(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    processed = processed_dir(root)
    lines = pd.read_csv(processed / "cas_us_linies.csv")
    clients = pd.read_csv(processed / "cas_us_clients.csv")
    geo = pd.read_csv(processed / "clients_geo.csv")
    order = infer_original_order(root)
    depot = geo[geo["client_id"].map(clean_client_id) == 0].iloc[0].to_dict()
    route_points = pd.concat(
        [pd.DataFrame([depot]), order, pd.DataFrame([depot])],
        ignore_index=True,
        sort=False,
    )

    try:
        road_km, driving_h, geometry, distance_source = request_osrm_route(route_points)
        osrm_note = "Distància i temps de conducció calculats amb OSRM"
    except Exception as exc:
        print(f"Warning: OSRM unavailable; using labelled Haversine fallback ({exc}).")
        road_km, driving_h = fallback_distance_duration(route_points)
        geometry = fallback_geometry(route_points)
        distance_source = "haversine_fallback"
        osrm_note = "OSRM no disponible en aquesta execució; fallback Haversine x1.3 etiquetat"

    unload_h = EXPECTED_CLIENTS * 15 / 60
    original_order = [0] + [clean_client_id(v) for v in order["client_id"]] + [0]
    baseline = {
        "transport_id": TRANSPORT_ID,
        "data": ROUTE_DATE,
        "ruta": ROUTE_ID,
        "repartidor": DRIVER_ID,
        "n_clients": EXPECTED_CLIENTS,
        "n_linies_comanda": int(len(lines)),
        "palets_totals": round(float(clients["palets"].sum()), 2),
        "km_carretera_osrm": round(float(road_km), 2),
        "temps_conduccio_h_osrm": round(float(driving_h), 2),
        "temps_descarrega_h": round(float(unload_h), 2),
        "temps_total_h": round(float(driving_h + unload_h), 2),
        "temps_descarrega_per_client_min": 15,
        "ordre_original": original_order,
        "distance_source": distance_source,
        "suposits": [
            "Ordre de visita inferit a partir del camp Entrega",
            osrm_note,
            "Temps de descàrrega estimat en 15 minuts per client",
        ],
    }
    (processed / "baseline_real.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    geometry_feature = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "transport_id": TRANSPORT_ID,
                    "ruta": ROUTE_ID,
                    "data": ROUTE_DATE,
                    "distance_source": distance_source,
                },
                "geometry": geometry,
            }
        ],
    }
    (processed / "baseline_route_geometry.geojson").write_text(
        json.dumps(geometry_feature, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    validate_baseline(root)
    return baseline


def validate_baseline(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    processed = processed_dir(root)
    geo = pd.read_csv(processed / "clients_geo.csv")
    baseline = json.loads((processed / "baseline_real.json").read_text(encoding="utf-8"))
    geo_ids = {clean_client_id(v) for v in geo["client_id"]}
    non_zero_order = [clean_client_id(v) for v in baseline["ordre_original"] if clean_client_id(v) != 0]
    checks = {
        "data": baseline["data"],
        "ruta": baseline["ruta"],
        "transport_id": baseline["transport_id"],
        "repartidor": baseline["repartidor"],
        "n_clients": baseline["n_clients"],
        "n_linies_comanda": baseline["n_linies_comanda"],
        "ordre_starts_depot": clean_client_id(baseline["ordre_original"][0]) == 0,
        "ordre_ends_depot": clean_client_id(baseline["ordre_original"][-1]) == 0,
        "non_zero_clients_in_order": len(non_zero_order),
        "unique_non_zero_clients": len(non_zero_order) == len(set(non_zero_order)),
        "order_clients_in_geo": set(non_zero_order).issubset(geo_ids),
    }
    assert checks["data"] == ROUTE_DATE
    assert checks["ruta"] == ROUTE_ID
    assert checks["transport_id"] == TRANSPORT_ID
    assert checks["repartidor"] == DRIVER_ID
    assert checks["n_clients"] == EXPECTED_CLIENTS
    assert checks["n_linies_comanda"] == EXPECTED_LINES
    assert checks["ordre_starts_depot"] and checks["ordre_ends_depot"]
    assert checks["non_zero_clients_in_order"] == EXPECTED_CLIENTS
    assert checks["unique_non_zero_clients"]
    assert checks["order_clients_in_geo"]
    return checks


def build_map(root: Path | None = None) -> Path:
    root = root or repo_root()
    processed = processed_dir(root)
    output = outputs_dir(root) / "mapa_baseline.html"
    legacy_output = processed / "mapa_baseline.html"

    import folium

    geo = pd.read_csv(processed / "clients_geo.csv")
    baseline = json.loads((processed / "baseline_real.json").read_text(encoding="utf-8"))
    geometry = json.loads((processed / "baseline_route_geometry.geojson").read_text(encoding="utf-8"))
    order = [clean_client_id(v) for v in baseline["ordre_original"]]
    order_lookup = {client_id: i for i, client_id in enumerate([v for v in order if v != 0], start=1)}

    m = folium.Map(
        location=[float(geo["lat"].mean()), float(geo["lon"].mean())],
        zoom_start=11,
        tiles="OpenStreetMap",
    )
    folium.GeoJson(
        geometry,
        name="Baseline OSRM route",
        style_function=lambda _: {"color": "#0f766e", "weight": 5, "opacity": 0.85},
    ).add_to(m)

    depot = geo[geo["client_id"].map(clean_client_id) == 0].iloc[0]
    folium.Marker(
        [depot["lat"], depot["lon"]],
        popup="<b>0. DDI Mollet</b><br>Depot",
        tooltip="Depot - DDI Mollet",
        icon=folium.Icon(color="black", icon="home", prefix="fa"),
    ).add_to(m)

    for _, row in geo[geo["client_id"].map(clean_client_id) != 0].iterrows():
        client_id = clean_client_id(row["client_id"])
        stop = order_lookup.get(client_id)
        popup = (
            f"<b>{stop}. {row['nom']}</b><br>"
            f"Client: {client_id}<br>"
            f"Town: {row['poblacio']}<br>"
            f"Zone: {row.get('zona_nom', '')}<br>"
            f"Palets: {row['palets']}"
        )
        folium.Marker(
            [row["lat"], row["lon"]],
            popup=popup,
            tooltip=f"{stop}. {row['nom']}",
            icon=folium.DivIcon(
                html=(
                    "<div style='background:#1d4ed8;color:white;border-radius:14px;"
                    "width:28px;height:28px;line-height:28px;text-align:center;"
                    "font-weight:700;border:2px solid white;box-shadow:0 1px 4px #555;'>"
                    f"{stop}</div>"
                )
            ),
        ).add_to(m)

    legend = f"""
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999;
      background: white; padding: 12px 14px; border: 1px solid #999;
      border-radius: 6px; font-size: 13px; box-shadow: 0 1px 5px rgba(0,0,0,.25);">
      <b>Baseline route {ROUTE_ID}</b><br>
      Date: {ROUTE_DATE}<br>
      Clients: {baseline['n_clients']}<br>
      OSRM km: {baseline['km_carretera_osrm']}<br>
      Total time: {baseline['temps_total_h']} h
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend))
    folium.LayerControl().add_to(m)
    m.save(output)
    m.save(legacy_output)
    return output


def validate_deliverables(root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    processed = processed_dir(root)
    outputs = outputs_dir(root)
    required = [
        processed / "cas_us_linies.csv",
        processed / "cas_us_clients.csv",
        processed / "clients_geo.csv",
        processed / "baseline_real.json",
        processed / "baseline_route_geometry.geojson",
        outputs / "mapa_baseline.html",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing deliverables: {missing}")
    checks = {}
    checks.update(validate_extraction(root))
    checks.update(validate_geocoding(root))
    checks.update(validate_baseline(root))
    checks["map_exists"] = (outputs / "mapa_baseline.html").exists()
    assert checks["map_exists"]
    return checks


def rebuild_all(root: Path | None = None, use_nominatim: bool = True) -> dict[str, Any]:
    root = root or repo_root()
    extract_case(root)
    geocode_clients(root, use_nominatim=use_nominatim)
    build_baseline(root)
    build_map(root)
    return validate_deliverables(root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild P1 route data, geocoding, baseline and map.")
    parser.add_argument("--root", type=Path, default=repo_root())
    parser.add_argument(
        "--skip-nominatim",
        action="store_true",
        help="Use manual reviewed fallbacks directly when Nominatim is rate-limited.",
    )
    args = parser.parse_args()
    checks = rebuild_all(args.root.resolve(), use_nominatim=not args.skip_nominatim)
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
