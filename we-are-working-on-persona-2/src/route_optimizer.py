#!/usr/bin/env python3
"""Single-truck route optimizer for the Damm SmartTruck DR0027 demo.

The module is intentionally small and robust for hackathon use:
- OR-Tools VRPTW first when available.
- OR-Tools TSP retry if time windows are infeasible.
- Nearest-neighbor fallback if OR-Tools is unavailable or fails.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


ROUTE_META = {
    "route_id": "DR0027",
    "load_number": "11764300",
    "date": "2026-05-08",
    "driver": "FRAN ROMERO",
}

DEPOT_ID = "DEPOT"
DEPOT_NAME = "DDI Mollet"
DEPOT_ADDRESS = "C/Molí de Can Bassa, Nau Damm 1, Mollet del Vallès"
DEPOT_TOWN = "Mollet del Vallès"
DEPOT_LAT = 41.5427
DEPOT_LON = 2.2135

AVERAGE_SPEED_KMH = 35
DEFAULT_SERVICE_MIN = 8
DEFAULT_TW_START = "08:00"
DEFAULT_TW_END = "18:00"

KNOWN_TOTAL_DELIVERY_UNITS = 837
KNOWN_TOTAL_RETURN_UNITS = 259
KNOWN_TOTAL_DELIVERY_WEIGHT_KG = 4719.120
KNOWN_TOTAL_RETURN_WEIGHT_KG = 2094.276

OLD_INPUT_COLUMNS = [
    "client_id",
    "client_name",
    "address",
    "town",
    "lat",
    "lon",
    "demand_units",
    "time_window_start",
    "time_window_end",
    "service_min",
    "original_order",
    "total_proforma",
    "total_cash",
]

NEW_INPUT_COLUMNS = [
    "client_id",
    "nom",
    "poblacio",
    "lat",
    "lon",
    "palets",
    "finestra_inici",
    "finestra_fi",
]

ZONE_NAME_COLUMN_CANDIDATES = [
    "ZonaTransp.1",
    "ZonaTranspNombre",
    "zona_transp_nombre",
    "zona_nombre",
    "zone_name",
    "nombre_zona",
    "Zona Entrega",
]

ZONE_CODE_COLUMN_CANDIDATES = [
    "ZonaTransp",
    "zona_transp",
    "zonaTransp",
    "ZONATRANSP",
    "Zona Transporte",
    "zona_transporte",
    "transport_zone",
    "zona",
    "zone",
]

ESTIMATED_RETURN_CONFIDENCE = "estimated_from_route_total_and_palets"
MACRO_ZONE_DISTANCE_THRESHOLD_KM = 12.0
MACRO_ZONE_MAX_DEMAND_UNITS = 8.0
TIME_WINDOW_STATUS_SEVERITY = {
    "on_time": 0,
    "early_wait": 1,
    "unknown": 2,
    "late": 3,
}
OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class Client:
    client_id: str
    client_name: str
    address: str
    town: str
    zone: str
    zone_code: str
    zone_source: str
    lat: float
    lon: float
    demand_units: float
    time_window_start: str
    time_window_end: str
    service_min: int
    original_order: Optional[int]
    total_proforma: float
    total_cash: float
    delivery_units: Optional[int]
    expected_return_units: Optional[int]
    return_confidence: str


@dataclass(frozen=True)
class RouteSolution:
    solver: str
    sequence: List[int]
    arrival_by_node: Dict[int, int]
    route_start_min: Optional[int] = None
    route_end_min: Optional[int] = None


@dataclass(frozen=True)
class DistanceData:
    distance_matrix: List[List[int]]
    travel_time_matrix: List[List[int]]
    provider: str
    warnings: List[str]


def _clean(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def _parse_float(value: object, default: float = 0.0) -> float:
    text = _clean(value).replace(",", ".")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _parse_int(value: object, default: int = 0) -> int:
    text = _clean(value)
    if not text:
        return default
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        return default


def _parse_optional_int(value: object) -> Optional[int]:
    text = _clean(value)
    if not text:
        return None
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        return None


def parse_time_text(value: object) -> Optional[int]:
    text = _clean(value)
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour * 60 + minute
    return None


def parse_hhmm(value: object, default: str) -> int:
    """Return minutes since midnight, falling back to a safe default."""
    parsed = parse_time_text(value)
    if parsed is not None:
        return parsed
    return parse_hhmm(default, "08:00")


def parse_hhmm_strict(value: object) -> Optional[int]:
    """Return minutes since midnight, or None when the value is missing/invalid."""
    return parse_time_text(value)


def format_hhmm(minutes_since_midnight: int) -> str:
    minutes_since_midnight %= 24 * 60
    hour = minutes_since_midnight // 60
    minute = minutes_since_midnight % 60
    return f"{hour:02d}:{minute:02d}"


def normalize_time_window(start: str, end: str) -> Tuple[str, str]:
    start = _clean(start) or DEFAULT_TW_START
    end = _clean(end) or DEFAULT_TW_END
    return start, end


def is_depot_id(client_id: object) -> bool:
    text = _clean(client_id).upper()
    return text in {DEPOT_ID, "0"}


def json_number(value: float) -> object:
    return int(value) if float(value).is_integer() else round(value, 3)


def normalize_header(value: str) -> str:
    return _clean(value).lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def detect_column(fieldnames: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate in fieldnames:
            return candidate

    normalized_candidates = {normalize_header(candidate) for candidate in candidates}
    for fieldname in fieldnames:
        if normalize_header(fieldname) in normalized_candidates:
            return fieldname
    return None


def detect_zone_name_column(fieldnames: Sequence[str]) -> Optional[str]:
    return detect_column(fieldnames, ZONE_NAME_COLUMN_CANDIDATES)


def detect_zone_code_column(fieldnames: Sequence[str]) -> Optional[str]:
    return detect_column(fieldnames, ZONE_CODE_COLUMN_CANDIDATES)


def normalize_zone(value: object, fallback: object) -> str:
    return _clean(value) or _clean(fallback) or "UNKNOWN"


def resolve_zone(
    row: Dict[str, str],
    zone_name_column: Optional[str],
    zone_code_column: Optional[str],
    town: str,
) -> Tuple[str, str, str]:
    zone_code = _clean(row.get(zone_code_column)) if zone_code_column else ""
    if zone_name_column:
        zone = _clean(row.get(zone_name_column))
        if zone:
            return zone, zone_code, "ZonaTransp.1"
    if zone_code:
        return zone_code, zone_code, "ZonaTransp"
    return normalize_zone("", town), "", "poblacio_fallback"


def depot_client() -> Client:
    return Client(
        client_id=DEPOT_ID,
        client_name=DEPOT_NAME,
        address=DEPOT_ADDRESS,
        town=DEPOT_TOWN,
        zone=DEPOT_TOWN,
        zone_code="",
        zone_source="poblacio_fallback",
        lat=DEPOT_LAT,
        lon=DEPOT_LON,
        demand_units=0,
        time_window_start=DEFAULT_TW_START,
        time_window_end=DEFAULT_TW_END,
        service_min=0,
        original_order=0,
        total_proforma=0.0,
        total_cash=0.0,
        delivery_units=0,
        expected_return_units=0,
        return_confidence="depot",
    )


def detect_input_format(fieldnames: Sequence[str]) -> str:
    fields = set(fieldnames)
    if set(NEW_INPUT_COLUMNS).issubset(fields):
        return "clients_geo"
    if set(OLD_INPUT_COLUMNS).issubset(fields):
        return "mock"

    old_missing = sorted(set(OLD_INPUT_COLUMNS) - fields)
    new_missing = sorted(set(NEW_INPUT_COLUMNS) - fields)
    raise ValueError(
        "Input CSV does not match a supported format. "
        f"Missing for mock format: {', '.join(old_missing) or 'none'}. "
        f"Missing for clients_geo format: {', '.join(new_missing) or 'none'}."
    )


def client_from_mock_row(
    row: Dict[str, str],
    zone_name_column: Optional[str],
    zone_code_column: Optional[str],
) -> Client:
    tw_start, tw_end = normalize_time_window(
        row.get("time_window_start", ""),
        row.get("time_window_end", ""),
    )

    client_id = _clean(row.get("client_id"))
    is_depot = is_depot_id(client_id)
    delivery_units = _parse_optional_int(row.get("delivery_units"))
    expected_return_units = _parse_optional_int(row.get("expected_return_units"))
    town = _clean(row.get("town")) or (DEPOT_TOWN if is_depot else "")
    zone, zone_code, zone_source = resolve_zone(row, zone_name_column, zone_code_column, town)

    return Client(
        client_id=client_id or DEPOT_ID if is_depot else client_id,
        client_name=_clean(row.get("client_name")) or (DEPOT_NAME if is_depot else client_id),
        address=_clean(row.get("address")) or (DEPOT_ADDRESS if is_depot else ""),
        town=town,
        zone=zone,
        zone_code=zone_code,
        zone_source=zone_source,
        lat=_parse_float(row.get("lat"), DEPOT_LAT if is_depot else math.nan),
        lon=_parse_float(row.get("lon"), DEPOT_LON if is_depot else math.nan),
        demand_units=_parse_float(row.get("demand_units"), 0.0),
        time_window_start=tw_start,
        time_window_end=tw_end,
        service_min=max(0, _parse_int(row.get("service_min"), 0 if is_depot else DEFAULT_SERVICE_MIN)),
        original_order=_parse_optional_int(row.get("original_order")),
        total_proforma=round(_parse_float(row.get("total_proforma"), 0.0), 2),
        total_cash=round(_parse_float(row.get("total_cash"), 0.0), 2),
        delivery_units=0 if is_depot else None if delivery_units is None else max(0, delivery_units),
        expected_return_units=0 if is_depot else None if expected_return_units is None else max(0, expected_return_units),
        return_confidence=(
            "depot"
            if is_depot
            else _clean(row.get("return_confidence")) or ("provided" if expected_return_units is not None else "")
        ),
    )


def client_from_clients_geo_row(
    row: Dict[str, str],
    zone_name_column: Optional[str],
    zone_code_column: Optional[str],
) -> Client:
    tw_start, tw_end = normalize_time_window(
        row.get("finestra_inici", ""),
        row.get("finestra_fi", ""),
    )

    client_id = _clean(row.get("client_id"))
    is_depot = is_depot_id(client_id)
    delivery_units = _parse_optional_int(row.get("delivery_units"))
    expected_return_units = _parse_optional_int(row.get("expected_return_units"))
    town = _clean(row.get("poblacio")) or (DEPOT_TOWN if is_depot else "")
    zone, zone_code, zone_source = resolve_zone(row, zone_name_column, zone_code_column, town)

    return Client(
        client_id=client_id or DEPOT_ID if is_depot else client_id,
        client_name=_clean(row.get("nom")) or (DEPOT_NAME if is_depot else client_id),
        address=_clean(row.get("address")) or (DEPOT_ADDRESS if is_depot else ""),
        town=town,
        zone=zone,
        zone_code=zone_code,
        zone_source=zone_source,
        lat=_parse_float(row.get("lat"), DEPOT_LAT if is_depot else math.nan),
        lon=_parse_float(row.get("lon"), DEPOT_LON if is_depot else math.nan),
        demand_units=_parse_float(row.get("palets"), 0.0),
        time_window_start=tw_start,
        time_window_end=tw_end,
        service_min=max(0, _parse_int(row.get("service_min"), 0 if is_depot else DEFAULT_SERVICE_MIN)),
        original_order=_parse_optional_int(row.get("original_order")),
        total_proforma=round(_parse_float(row.get("total_proforma"), 0.0), 2),
        total_cash=round(_parse_float(row.get("total_cash"), 0.0), 2),
        delivery_units=0 if is_depot else None if delivery_units is None else max(0, delivery_units),
        expected_return_units=0 if is_depot else None if expected_return_units is None else max(0, expected_return_units),
        return_confidence=(
            "depot"
            if is_depot
            else _clean(row.get("return_confidence")) or ("provided" if expected_return_units is not None else "")
        ),
    )


def load_clients(csv_path: Path) -> List[Client]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        fieldnames = reader.fieldnames or []
        input_format = detect_input_format(fieldnames)
        zone_name_column = detect_zone_name_column(fieldnames)
        zone_code_column = detect_zone_code_column(fieldnames)
        if input_format == "clients_geo":
            rows = [client_from_clients_geo_row(row, zone_name_column, zone_code_column) for row in reader]
        else:
            rows = [client_from_mock_row(row, zone_name_column, zone_code_column) for row in reader]

    if not rows:
        raise ValueError(f"Input CSV contains no client rows: {csv_path}")

    for client in rows:
        if math.isnan(client.lat) or math.isnan(client.lon):
            raise ValueError(f"Client {client.client_id} has invalid coordinates")

    depot_rows = [client for client in rows if is_depot_id(client.client_id)]
    client_rows = [client for client in rows if not is_depot_id(client.client_id)]

    depot = depot_rows[0] if depot_rows else depot_client()
    if depot.service_min != 0:
        depot = Client(
            client_id=depot.client_id,
            client_name=depot.client_name,
            address=depot.address,
            town=depot.town,
            zone=depot.zone,
            zone_code=depot.zone_code,
            zone_source=depot.zone_source,
            lat=depot.lat,
            lon=depot.lon,
            demand_units=depot.demand_units,
            time_window_start=depot.time_window_start,
            time_window_end=depot.time_window_end,
            service_min=0,
            original_order=depot.original_order,
            total_proforma=depot.total_proforma,
            total_cash=depot.total_cash,
            delivery_units=0,
            expected_return_units=0,
            return_confidence="depot",
        )

    return [depot] + client_rows


def haversine_meters(a: Client, b: Client) -> int:
    radius_m = 6_371_000
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    delta_lat = math.radians(b.lat - a.lat)
    delta_lon = math.radians(b.lon - a.lon)

    h = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return int(round(2 * radius_m * math.asin(math.sqrt(h))))


def haversine_points_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> int:
    radius_m = 6_371_000
    lat1 = math.radians(lat_a)
    lat2 = math.radians(lat_b)
    delta_lat = math.radians(lat_b - lat_a)
    delta_lon = math.radians(lon_b - lon_a)
    h = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return int(round(2 * radius_m * math.asin(math.sqrt(h))))


def build_distance_matrix(clients: Sequence[Client]) -> List[List[int]]:
    return [[haversine_meters(origin, destination) for destination in clients] for origin in clients]


def build_travel_time_matrix(distance_matrix: Sequence[Sequence[int]], speed_kmh: int) -> List[List[int]]:
    meters_per_minute = speed_kmh * 1000 / 60
    matrix: List[List[int]] = []
    for row in distance_matrix:
        matrix.append([0 if meters == 0 else int(math.ceil(meters / meters_per_minute)) for meters in row])
    return matrix


def build_haversine_matrices(clients: Sequence[Client], warnings: Optional[List[str]] = None) -> DistanceData:
    distance_matrix = build_distance_matrix(clients)
    return DistanceData(
        distance_matrix=distance_matrix,
        travel_time_matrix=build_travel_time_matrix(distance_matrix, AVERAGE_SPEED_KMH),
        provider="haversine",
        warnings=warnings or [],
    )


def fetch_osrm_matrices(clients: Sequence[Client]) -> Tuple[List[List[int]], List[List[int]]]:
    coordinates = ";".join(f"{client.lon:.6f},{client.lat:.6f}" for client in clients)
    query = urllib.parse.urlencode({"annotations": "distance,duration"})
    url = f"{OSRM_BASE_URL}/table/v1/driving/{coordinates}?{query}"

    context = None
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        context = None

    with urllib.request.urlopen(url, timeout=OSRM_TIMEOUT_SECONDS, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("code") != "Ok":
        raise ValueError(f"OSRM returned code {payload.get('code')!r}")

    distances = payload.get("distances")
    durations = payload.get("durations")
    expected_size = len(clients)
    if not isinstance(distances, list) or not isinstance(durations, list):
        raise ValueError("OSRM response is missing distance or duration matrices")
    if len(distances) != expected_size or len(durations) != expected_size:
        raise ValueError("OSRM matrix size does not match input coordinates")

    distance_matrix: List[List[int]] = []
    travel_time_matrix: List[List[int]] = []
    for row_index, (distance_row, duration_row) in enumerate(zip(distances, durations)):
        if not isinstance(distance_row, list) or not isinstance(duration_row, list):
            raise ValueError("OSRM matrix rows are invalid")
        if len(distance_row) != expected_size or len(duration_row) != expected_size:
            raise ValueError("OSRM matrix row size does not match input coordinates")

        matrix_distance_row = []
        matrix_time_row = []
        for col_index, (distance_m, duration_s) in enumerate(zip(distance_row, duration_row)):
            if distance_m is None or duration_s is None:
                raise ValueError(f"OSRM matrix has missing cell at {row_index},{col_index}")
            try:
                distance_value = float(distance_m)
                duration_value = float(duration_s)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"OSRM matrix has invalid cell at {row_index},{col_index}") from exc
            if not math.isfinite(distance_value) or not math.isfinite(duration_value):
                raise ValueError(f"OSRM matrix has non-finite cell at {row_index},{col_index}")

            matrix_distance_row.append(int(round(distance_value)))
            matrix_time_row.append(0 if duration_value == 0 else int(math.ceil(duration_value / 60)))

        distance_matrix.append(matrix_distance_row)
        travel_time_matrix.append(matrix_time_row)

    return distance_matrix, travel_time_matrix


def build_distance_data(clients: Sequence[Client], requested_provider: str) -> DistanceData:
    if requested_provider == "haversine":
        return build_haversine_matrices(clients)

    try:
        distance_matrix, travel_time_matrix = fetch_osrm_matrices(clients)
        return DistanceData(
            distance_matrix=distance_matrix,
            travel_time_matrix=travel_time_matrix,
            provider="osrm",
            warnings=[],
        )
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        warning = f"OSRM distance provider failed ({exc}); falling back to Haversine."
        print(f"WARNING: {warning}")
        return build_haversine_matrices(clients, warnings=[warning])


def solve_with_ortools(
    clients: Sequence[Client],
    distance_matrix: Sequence[Sequence[int]],
    travel_time_matrix: Sequence[Sequence[int]],
    use_time_windows: bool,
) -> Optional[RouteSolution]:
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError:
        return None

    try:
        manager = pywrapcp.RoutingIndexManager(len(clients), 1, 0)
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_index: int, to_index: int) -> int:
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            return int(distance_matrix[from_node][to_node])

        distance_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(distance_callback_index)

        time_dimension = None
        if use_time_windows:
            service_times = [client.service_min for client in clients]

            def time_callback(from_index: int, to_index: int) -> int:
                from_node = manager.IndexToNode(from_index)
                to_node = manager.IndexToNode(to_index)
                return int(service_times[from_node] + travel_time_matrix[from_node][to_node])

            time_callback_index = routing.RegisterTransitCallback(time_callback)
            routing.AddDimension(
                time_callback_index,
                12 * 60,  # Waiting slack keeps realistic time windows from breaking the demo.
                24 * 60,
                False,
                "Time",
            )
            time_dimension = routing.GetDimensionOrDie("Time")

            for node_index, client in enumerate(clients):
                start_min = parse_hhmm(client.time_window_start, DEFAULT_TW_START)
                end_min = parse_hhmm(client.time_window_end, DEFAULT_TW_END)
                if end_min < start_min:
                    end_min += 24 * 60

                if node_index == 0:
                    time_dimension.CumulVar(routing.Start(0)).SetRange(start_min, end_min)
                    time_dimension.CumulVar(routing.End(0)).SetRange(start_min, end_min)
                else:
                    time_dimension.CumulVar(manager.NodeToIndex(node_index)).SetRange(start_min, end_min)

            routing.AddVariableMinimizedByFinalizer(time_dimension.CumulVar(routing.Start(0)))
            routing.AddVariableMinimizedByFinalizer(time_dimension.CumulVar(routing.End(0)))

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_parameters.time_limit.seconds = 8
        search_parameters.log_search = False

        solution = routing.SolveWithParameters(search_parameters)
        if solution is None:
            return None

        sequence: List[int] = []
        arrival_by_node: Dict[int, int] = {}
        index = routing.Start(0)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            sequence.append(node)
            if time_dimension is not None:
                arrival_by_node[node] = int(solution.Value(time_dimension.CumulVar(index)))
            index = solution.Value(routing.NextVar(index))

        sequence.append(manager.IndexToNode(index))
        route_start_min = None
        route_end_min = None
        if time_dimension is not None:
            route_start_min = int(solution.Value(time_dimension.CumulVar(routing.Start(0))))
            route_end_min = int(solution.Value(time_dimension.CumulVar(routing.End(0))))

        return RouteSolution(
            solver="ortools_vrptw" if use_time_windows else "ortools_tsp",
            sequence=sequence,
            arrival_by_node=arrival_by_node,
            route_start_min=route_start_min,
            route_end_min=route_end_min,
        )
    except Exception:
        return None


def nearest_neighbor_solution(distance_matrix: Sequence[Sequence[int]]) -> RouteSolution:
    unvisited = set(range(1, len(distance_matrix)))
    sequence = [0]
    current = 0

    while unvisited:
        next_node = min(unvisited, key=lambda node: distance_matrix[current][node])
        sequence.append(next_node)
        unvisited.remove(next_node)
        current = next_node

    sequence.append(0)
    return RouteSolution(solver="nearest_neighbor_fallback", sequence=sequence, arrival_by_node={})


def optimize_route(
    clients: Sequence[Client],
    distance_matrix: Sequence[Sequence[int]],
    travel_time_matrix: Sequence[Sequence[int]],
) -> RouteSolution:
    solution = solve_with_ortools(clients, distance_matrix, travel_time_matrix, use_time_windows=True)
    if solution is not None:
        return solution

    solution = solve_with_ortools(clients, distance_matrix, travel_time_matrix, use_time_windows=False)
    if solution is not None:
        return solution

    return nearest_neighbor_solution(distance_matrix)


def route_distance_m(sequence: Sequence[int], distance_matrix: Sequence[Sequence[int]]) -> int:
    return sum(distance_matrix[sequence[index]][sequence[index + 1]] for index in range(len(sequence) - 1))


def route_travel_time_min(sequence: Sequence[int], travel_time_matrix: Sequence[Sequence[int]]) -> int:
    return sum(travel_time_matrix[sequence[index]][sequence[index + 1]] for index in range(len(sequence) - 1))


def apportion_units_exact(total_units: int, node_weights: Sequence[Tuple[int, float]]) -> Dict[int, int]:
    """Distribute integer units by weight while preserving the exact route total."""
    if not node_weights:
        return {}

    safe_weights = [(node, max(0.0, float(weight))) for node, weight in node_weights]
    weight_sum = sum(weight for _, weight in safe_weights)
    if weight_sum <= 0:
        safe_weights = [(node, 1.0) for node, _ in safe_weights]
        weight_sum = float(len(safe_weights))

    allocations: Dict[int, int] = {}
    remainders: List[Tuple[float, int]] = []
    allocated = 0
    for node, weight in safe_weights:
        raw_units = total_units * weight / weight_sum
        floor_units = int(math.floor(raw_units))
        allocations[node] = floor_units
        allocated += floor_units
        remainders.append((raw_units - floor_units, node))

    remaining = total_units - allocated
    for _, node in sorted(remainders, key=lambda item: (-item[0], item[1]))[:remaining]:
        allocations[node] += 1

    return allocations


def demand_weights_for_nodes(clients: Sequence[Client], nodes: Sequence[int]) -> List[Tuple[int, float]]:
    weights = [(node, float(max(0, clients[node].demand_units))) for node in nodes]
    if sum(weight for _, weight in weights) > 0:
        return weights
    return [(node, 1.0) for node in nodes]


def return_weights_for_nodes(clients: Sequence[Client], nodes: Sequence[int]) -> List[Tuple[int, float]]:
    return demand_weights_for_nodes(clients, nodes)


def resolve_delivery_units(clients: Sequence[Client], route_client_nodes: Sequence[int]) -> Dict[int, int]:
    provided = {
        node: clients[node].delivery_units
        for node in route_client_nodes
        if clients[node].delivery_units is not None
    }
    missing_nodes = [node for node in route_client_nodes if clients[node].delivery_units is None]
    provided_total = sum(value or 0 for value in provided.values())

    if not missing_nodes and provided_total == KNOWN_TOTAL_DELIVERY_UNITS:
        return {node: int(provided[node] or 0) for node in route_client_nodes}

    if missing_nodes and provided_total <= KNOWN_TOTAL_DELIVERY_UNITS:
        remaining_total = KNOWN_TOTAL_DELIVERY_UNITS - provided_total
        allocated_missing = apportion_units_exact(
            remaining_total,
            demand_weights_for_nodes(clients, missing_nodes),
        )
        return {
            node: int(provided[node] or 0) if node in provided else allocated_missing[node]
            for node in route_client_nodes
        }

    # Route-level Hoja de Carga total is authoritative; normalize inconsistent inputs.
    return apportion_units_exact(
        KNOWN_TOTAL_DELIVERY_UNITS,
        demand_weights_for_nodes(clients, route_client_nodes),
    )


def resolve_expected_returns(
    clients: Sequence[Client],
    route_client_nodes: Sequence[int],
) -> Tuple[Dict[int, int], Dict[int, str]]:
    provided = {
        node: clients[node].expected_return_units
        for node in route_client_nodes
        if clients[node].expected_return_units is not None
    }
    missing_nodes = [node for node in route_client_nodes if clients[node].expected_return_units is None]
    provided_total = sum(value or 0 for value in provided.values())

    if not missing_nodes and provided_total == KNOWN_TOTAL_RETURN_UNITS:
        return (
            {node: int(provided[node] or 0) for node in route_client_nodes},
            {node: clients[node].return_confidence or "provided" for node in route_client_nodes},
        )

    if missing_nodes and provided_total <= KNOWN_TOTAL_RETURN_UNITS:
        remaining_total = KNOWN_TOTAL_RETURN_UNITS - provided_total
        allocated_missing = apportion_units_exact(
            remaining_total,
            return_weights_for_nodes(clients, missing_nodes),
        )
        return_units = {
            node: int(provided[node] or 0) if node in provided else allocated_missing[node]
            for node in route_client_nodes
        }
        confidence = {
            node: clients[node].return_confidence or "provided" if node in provided else ESTIMATED_RETURN_CONFIDENCE
            for node in route_client_nodes
        }
        return return_units, confidence

    # If provided values are incomplete or inconsistent, keep the demo stable by re-estimating all stops.
    return_units = apportion_units_exact(
        KNOWN_TOTAL_RETURN_UNITS,
        return_weights_for_nodes(clients, route_client_nodes),
    )
    return return_units, {node: ESTIMATED_RETURN_CONFIDENCE for node in route_client_nodes}


def return_risk(expected_return_units: int) -> str:
    if expected_return_units >= 25:
        return "high"
    if expected_return_units >= 10:
        return "medium"
    return "low"


def load_risk(load_percent_after_stop: float) -> str:
    if load_percent_after_stop > 100:
        return "high"
    if load_percent_after_stop > 90:
        return "medium"
    return "low"


def time_window_status(
    time_window_start: str,
    time_window_end: str,
    eta_min: int,
    raw_arrival_min: int,
) -> str:
    window_start = parse_hhmm_strict(time_window_start)
    window_end = parse_hhmm_strict(time_window_end)
    if window_start is None or window_end is None:
        return "unknown"

    if window_end < window_start:
        window_end += 24 * 60
    if eta_min < window_start:
        return "early_wait"
    if eta_min > window_end:
        return "late"
    if raw_arrival_min < window_start <= eta_min:
        return "early_wait"
    return "on_time"


def build_load_simulation(ordered_stops: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    simulation = []
    current_load_units = KNOWN_TOTAL_DELIVERY_UNITS
    cumulative_delivered_units = 0
    cumulative_return_units = 0

    for stop in ordered_stops:
        delivery_units = int(stop["delivery_units"])
        expected_return_units = int(stop["expected_return_units"])
        cumulative_delivered_units += delivery_units
        cumulative_return_units += expected_return_units
        current_load_units -= delivery_units
        current_load_units += expected_return_units
        load_percent_after_stop = current_load_units / KNOWN_TOTAL_DELIVERY_UNITS * 100

        simulation.append(
            {
                "stop_number": stop["stop_number"],
                "client_id": stop["client_id"],
                "client_name": stop["client_name"],
                "delivery_units": delivery_units,
                "expected_return_units": expected_return_units,
                "cumulative_delivered_units": cumulative_delivered_units,
                "cumulative_return_units": cumulative_return_units,
                "load_units_after_stop": current_load_units,
                "load_percent_after_stop": round(load_percent_after_stop, 1),
                "reverse_logistics_risk": load_risk(load_percent_after_stop),
            }
        )

    return simulation


def build_time_window_summary(
    ordered_stops: Sequence[Dict[str, object]],
    solver: str,
) -> Dict[str, object]:
    return {
        "solver_enforced_time_windows": solver == "ortools_vrptw",
        "on_time_stops": sum(1 for stop in ordered_stops if stop["time_window_status"] == "on_time"),
        "early_wait_stops": sum(1 for stop in ordered_stops if stop["time_window_status"] == "early_wait"),
        "late_stops": sum(1 for stop in ordered_stops if stop["time_window_status"] == "late"),
        "unknown_stops": sum(1 for stop in ordered_stops if stop["time_window_status"] == "unknown"),
    }


def build_warnings(
    load_simulation: Sequence[Dict[str, object]],
    time_window_summary: Dict[str, object],
    solver: str,
    extra_warnings: Optional[Sequence[str]] = None,
) -> List[str]:
    warnings = list(extra_warnings or [])
    if any(stop["reverse_logistics_risk"] == "high" for stop in load_simulation):
        warnings.append(
            "At least one stop exceeds 100% simulated load after returns; review reverse logistics buffer assumptions."
        )

    return_ratio = KNOWN_TOTAL_RETURN_UNITS / KNOWN_TOTAL_DELIVERY_UNITS
    if return_ratio > 0.25:
        warnings.append(
            "Reverse logistics buffer is relevant: known route returns are 259 units against 837 delivery units."
        )

    if solver == "ortools_vrptw" and int(time_window_summary["late_stops"]) > 0:
        warnings.append("OR-Tools VRPTW reported a solution, but at least one exported stop is late.")
    elif solver != "ortools_vrptw":
        warnings.append("Time windows could not be enforced by the solver; statuses are informational.")

    return warnings


def distance_provider_note(provider: str) -> str:
    if provider == "osrm":
        return "Road distances/durations computed using OSRM public demo server."
    return "Straight-line Haversine distances used as fallback."


def build_zone_sequence(ordered_stops: Sequence[Dict[str, object]]) -> List[str]:
    sequence: List[str] = []
    seen = set()
    for stop in ordered_stops:
        zone = str(stop.get("zone") or "UNKNOWN")
        if zone not in seen:
            sequence.append(zone)
            seen.add(zone)
    return sequence


def count_zone_transitions(ordered_stops: Sequence[Dict[str, object]]) -> int:
    transitions = 0
    for index in range(len(ordered_stops) - 1):
        if ordered_stops[index].get("zone") != ordered_stops[index + 1].get("zone"):
            transitions += 1
    return transitions


def build_zone_summary(ordered_stops: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for stop in ordered_stops:
        grouped.setdefault(str(stop.get("zone") or "UNKNOWN"), []).append(stop)

    summary = []
    for zone in build_zone_sequence(ordered_stops):
        stops = grouped[zone]
        total_demand_units = sum(float(stop.get("demand_units") or 0) for stop in stops)
        total_delivery_units = sum(int(stop.get("delivery_units") or 0) for stop in stops)
        total_expected_return_units = sum(int(stop.get("expected_return_units") or 0) for stop in stops)
        summary.append(
            {
                "zone": zone,
                "number_of_stops": len(stops),
                "total_demand_units": json_number(total_demand_units),
                "total_delivery_units": total_delivery_units,
                "total_expected_return_units": total_expected_return_units,
                "first_stop_number": stops[0]["stop_number"],
                "last_stop_number": stops[-1]["stop_number"],
                "centroid_lat": round(sum(float(stop["lat"]) for stop in stops) / len(stops), 6),
                "centroid_lon": round(sum(float(stop["lon"]) for stop in stops) / len(stops), 6),
            }
        )

    return summary


def macro_zone_name(zones: Sequence[str], index: int) -> str:
    zone_set = set(zones)
    osona_east = {"CALLDETENES", "FOLGUEROLES", "SANT JULIÀ DE VILATORTA"}
    if len(zone_set & osona_east) >= 2:
        return "OSONA EAST"
    if "VIC" in zone_set:
        return "OSONA CENTRAL"
    return f"MACRO ZONE {index}"


def build_macro_zone_suggestions(zone_summary: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    suggestions = []
    assigned = set()
    candidate_zones = sorted(zone_summary, key=lambda item: str(item["zone"]))
    suggestion_index = 1

    for zone_info in candidate_zones:
        zone = str(zone_info["zone"])
        if zone in assigned:
            continue

        group = [zone_info]
        group_demand = float(zone_info["total_demand_units"])

        for other in candidate_zones:
            other_zone = str(other["zone"])
            if other_zone == zone or other_zone in assigned:
                continue
            if group_demand + float(other["total_demand_units"]) > MACRO_ZONE_MAX_DEMAND_UNITS:
                continue

            close_to_group = any(
                haversine_points_meters(
                    float(member["centroid_lat"]),
                    float(member["centroid_lon"]),
                    float(other["centroid_lat"]),
                    float(other["centroid_lon"]),
                )
                / 1000
                <= MACRO_ZONE_DISTANCE_THRESHOLD_KM
                for member in group
            )
            if close_to_group:
                group.append(other)
                group_demand += float(other["total_demand_units"])

        if len(group) < 2:
            continue

        group_zones = [str(item["zone"]) for item in group]
        assigned.update(group_zones)
        suggestions.append(
            {
                "macro_zone": macro_zone_name(group_zones, suggestion_index),
                "zones": group_zones,
                "reason": "Zones are geographically close and can be treated as one delivery block.",
                "total_stops": sum(int(item["number_of_stops"]) for item in group),
                "total_demand_units": json_number(sum(float(item["total_demand_units"]) for item in group)),
                "total_expected_return_units": sum(int(item["total_expected_return_units"]) for item in group),
            }
        )
        suggestion_index += 1

    return suggestions


def zone_explanation() -> str:
    return (
        "Route order is optimized at client level with OR-Tools. Zones are exported to make the result "
        "operationally understandable: the driver can follow Damm transport-zone blocks such as CENTRE, "
        "UNIVERSITA, AFORES, or ST.JULIA instead of isolated points, while towns remain display/location "
        "information. Expected returns are summarized by zone for capacity discussions."
    )


def payload_zone_source(clients: Sequence[Client]) -> str:
    non_depot_clients = [client for client in clients if not is_depot_id(client.client_id)]
    if any(client.zone_source == "ZonaTransp.1" for client in non_depot_clients):
        return "ZonaTransp.1"
    if any(client.zone_source == "ZonaTransp" for client in non_depot_clients):
        return "ZonaTransp"
    return "poblacio_fallback"


def build_zone_warnings(clients: Sequence[Client]) -> List[str]:
    non_depot_clients = [client for client in clients if not is_depot_id(client.client_id)]
    fallback_count = sum(1 for client in non_depot_clients if client.zone_source == "poblacio_fallback")
    if fallback_count == 0:
        return []
    if fallback_count == len(non_depot_clients):
        return ["ZonaTransp not found; using poblacio as zone fallback."]
    return [f"ZonaTransp missing for {fallback_count} clients; using poblacio as zone fallback for those stops."]


def compute_arrival_schedule(
    sequence: Sequence[int],
    clients: Sequence[Client],
    travel_time_matrix: Sequence[Sequence[int]],
    respect_time_windows: bool,
) -> Tuple[bool, Dict[int, int], int, int]:
    start_min = parse_hhmm(clients[0].time_window_start, DEFAULT_TW_START)
    end_limit = parse_hhmm(clients[0].time_window_end, DEFAULT_TW_END)
    if end_limit < start_min:
        end_limit += 24 * 60

    current_min = start_min
    previous_node = sequence[0]
    arrival_by_node: Dict[int, int] = {}

    for node in sequence[1:]:
        current_min += travel_time_matrix[previous_node][node]
        if node == 0:
            if respect_time_windows and current_min > end_limit:
                return False, {}, start_min, current_min
            return True, arrival_by_node, start_min, current_min

        if respect_time_windows:
            window_start = parse_hhmm(clients[node].time_window_start, DEFAULT_TW_START)
            window_end = parse_hhmm(clients[node].time_window_end, DEFAULT_TW_END)
            if window_end < window_start:
                window_end += 24 * 60
            if current_min < window_start:
                current_min = window_start
            if current_min > window_end:
                return False, {}, start_min, current_min

        arrival_by_node[node] = current_min
        current_min += clients[node].service_min
        previous_node = node

    return False, {}, start_min, current_min


def time_window_statuses_for_sequence(
    sequence: Sequence[int],
    clients: Sequence[Client],
    travel_time_matrix: Sequence[Sequence[int]],
    respect_time_windows: bool,
) -> Tuple[bool, Dict[int, str]]:
    current_departure_min = parse_hhmm(clients[0].time_window_start, DEFAULT_TW_START)
    previous_node = sequence[0]
    statuses: Dict[int, str] = {}

    for node in sequence[1:]:
        raw_arrival_min = current_departure_min + travel_time_matrix[previous_node][node]
        if node == 0:
            return True, statuses

        client = clients[node]
        eta_min = raw_arrival_min
        window_start = parse_hhmm_strict(client.time_window_start)
        if respect_time_windows and window_start is not None and eta_min < window_start:
            eta_min = window_start

        status = time_window_status(
            client.time_window_start,
            client.time_window_end,
            eta_min,
            raw_arrival_min,
        )
        statuses[node] = status
        if respect_time_windows and status == "late":
            return False, statuses

        current_departure_min = eta_min + client.service_min
        previous_node = node

    return False, statuses


def time_window_statuses_not_worse(
    baseline_statuses: Dict[int, str],
    candidate_statuses: Dict[int, str],
) -> bool:
    for node, baseline_status in baseline_statuses.items():
        candidate_status = candidate_statuses.get(node, "unknown")
        if TIME_WINDOW_STATUS_SEVERITY[candidate_status] > TIME_WINDOW_STATUS_SEVERITY[baseline_status]:
            return False
    return True


def zone_transitions_for_sequence(sequence: Sequence[int], clients: Sequence[Client]) -> int:
    client_nodes = [node for node in sequence[1:-1] if node != 0]
    transitions = 0
    for index in range(len(client_nodes) - 1):
        if clients[client_nodes[index]].zone != clients[client_nodes[index + 1]].zone:
            transitions += 1
    return transitions


def load_risk_score(
    sequence: Sequence[int],
    delivery_units_by_node: Dict[int, int],
    expected_returns_by_node: Dict[int, int],
) -> float:
    current_load_units = KNOWN_TOTAL_DELIVERY_UNITS
    score = 0.0
    for stop_position, node in enumerate(sequence[1:-1], start=1):
        current_load_units -= delivery_units_by_node[node]
        current_load_units += expected_returns_by_node[node]
        load_percent = current_load_units / KNOWN_TOTAL_DELIVERY_UNITS * 100
        if load_percent > 100:
            score += 10_000 + (load_percent - 100) * 100
        elif load_percent > 90:
            score += 100 + (load_percent - 90)

        # Earlier high loads matter more for truck-space risk during the route.
        score += max(0.0, load_percent - 85) * (1 / stop_position)

    return score


def apply_zone_aware_soft_optimization(
    clients: Sequence[Client],
    distance_matrix: Sequence[Sequence[int]],
    travel_time_matrix: Sequence[Sequence[int]],
    solution: RouteSolution,
) -> RouteSolution:
    route_client_nodes = solution.sequence[1:-1]
    if len(route_client_nodes) < 3:
        return solution

    delivery_units_by_node = resolve_delivery_units(clients, route_client_nodes)
    expected_returns_by_node, _ = resolve_expected_returns(clients, route_client_nodes)
    respect_time_windows = solution.solver == "ortools_vrptw"

    baseline_distance = route_distance_m(solution.sequence, distance_matrix)
    max_allowed_distance = baseline_distance * 1.05
    _, baseline_statuses = time_window_statuses_for_sequence(
        solution.sequence,
        clients,
        travel_time_matrix,
        respect_time_windows,
    )

    best_sequence = list(solution.sequence)
    best_transitions = zone_transitions_for_sequence(best_sequence, clients)
    best_risk_score = load_risk_score(best_sequence, delivery_units_by_node, expected_returns_by_node)
    best_distance = baseline_distance
    improved = True
    iterations = 0

    while improved and iterations < 3:
        improved = False
        iterations += 1
        iteration_best_sequence = best_sequence
        iteration_best_transitions = best_transitions
        iteration_best_risk_score = best_risk_score
        iteration_best_distance = best_distance

        for start in range(1, len(best_sequence) - 2):
            for end in range(start + 1, len(best_sequence) - 1):
                candidate = (
                    best_sequence[:start]
                    + list(reversed(best_sequence[start : end + 1]))
                    + best_sequence[end + 1 :]
                )
                candidate_distance = route_distance_m(candidate, distance_matrix)
                if candidate_distance > max_allowed_distance:
                    continue

                feasible, candidate_statuses = time_window_statuses_for_sequence(
                    candidate,
                    clients,
                    travel_time_matrix,
                    respect_time_windows,
                )
                if not feasible or not time_window_statuses_not_worse(baseline_statuses, candidate_statuses):
                    continue

                candidate_transitions = zone_transitions_for_sequence(candidate, clients)
                if candidate_transitions >= iteration_best_transitions:
                    continue

                candidate_risk_score = load_risk_score(candidate, delivery_units_by_node, expected_returns_by_node)
                if candidate_risk_score > best_risk_score:
                    continue

                iteration_best_sequence = candidate
                iteration_best_transitions = candidate_transitions
                iteration_best_risk_score = candidate_risk_score
                iteration_best_distance = candidate_distance
                improved = True

        best_sequence = iteration_best_sequence
        best_transitions = iteration_best_transitions
        best_risk_score = iteration_best_risk_score
        best_distance = iteration_best_distance

    if best_sequence == solution.sequence:
        return solution

    feasible, arrival_by_node, route_start_min, route_end_min = compute_arrival_schedule(
        best_sequence,
        clients,
        travel_time_matrix,
        respect_time_windows,
    )
    if not feasible:
        return solution

    return RouteSolution(
        solver=solution.solver,
        sequence=best_sequence,
        arrival_by_node=arrival_by_node,
        route_start_min=route_start_min,
        route_end_min=route_end_min,
    )


def apply_return_aware_soft_optimization(
    clients: Sequence[Client],
    distance_matrix: Sequence[Sequence[int]],
    travel_time_matrix: Sequence[Sequence[int]],
    solution: RouteSolution,
) -> RouteSolution:
    route_client_nodes = solution.sequence[1:-1]
    if len(route_client_nodes) < 3:
        return solution

    delivery_units_by_node = resolve_delivery_units(clients, route_client_nodes)
    expected_returns_by_node, _ = resolve_expected_returns(clients, route_client_nodes)
    respect_time_windows = solution.solver == "ortools_vrptw"

    baseline_distance = route_distance_m(solution.sequence, distance_matrix)
    max_allowed_distance = baseline_distance * 1.05
    best_sequence = list(solution.sequence)
    best_distance = baseline_distance
    best_score = load_risk_score(best_sequence, delivery_units_by_node, expected_returns_by_node)
    improved = True
    iterations = 0

    while improved and iterations < 3:
        improved = False
        iterations += 1
        iteration_best_sequence = best_sequence
        iteration_best_distance = best_distance
        iteration_best_score = best_score

        # Reversing short or long segments gives us adjacent swaps and simple 2-opt candidates.
        for start in range(1, len(best_sequence) - 2):
            for end in range(start + 1, len(best_sequence) - 1):
                candidate = (
                    best_sequence[:start]
                    + list(reversed(best_sequence[start : end + 1]))
                    + best_sequence[end + 1 :]
                )
                candidate_distance = route_distance_m(candidate, distance_matrix)
                if candidate_distance > max_allowed_distance:
                    continue

                feasible, _, _, _ = compute_arrival_schedule(
                    candidate,
                    clients,
                    travel_time_matrix,
                    respect_time_windows,
                )
                if not feasible:
                    continue

                candidate_score = load_risk_score(candidate, delivery_units_by_node, expected_returns_by_node)
                if candidate_score < iteration_best_score:
                    iteration_best_sequence = candidate
                    iteration_best_distance = candidate_distance
                    iteration_best_score = candidate_score
                    improved = True

        best_sequence = iteration_best_sequence
        best_distance = iteration_best_distance
        best_score = iteration_best_score

    if best_sequence == solution.sequence:
        return solution

    feasible, arrival_by_node, route_start_min, route_end_min = compute_arrival_schedule(
        best_sequence,
        clients,
        travel_time_matrix,
        respect_time_windows,
    )
    if not feasible:
        return solution

    return RouteSolution(
        solver=solution.solver,
        sequence=best_sequence,
        arrival_by_node=arrival_by_node,
        route_start_min=route_start_min,
        route_end_min=route_end_min,
    )


def build_output_payload(
    clients: Sequence[Client],
    distance_matrix: Sequence[Sequence[int]],
    travel_time_matrix: Sequence[Sequence[int]],
    solution: RouteSolution,
    distance_provider: str,
    distance_provider_warnings: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    total_distance_m = route_distance_m(solution.sequence, distance_matrix)
    total_travel_min = route_travel_time_min(solution.sequence, travel_time_matrix)
    total_service_min = sum(clients[node].service_min for node in solution.sequence if node != 0)

    if solution.route_start_min is not None and solution.route_end_min is not None:
        estimated_time_min = solution.route_end_min - solution.route_start_min
    else:
        estimated_time_min = total_travel_min + total_service_min

    ordered_stops = []
    route_client_nodes = solution.sequence[1:-1]
    delivery_units_by_node = resolve_delivery_units(clients, route_client_nodes)
    expected_returns_by_node, return_confidence_by_node = resolve_expected_returns(clients, route_client_nodes)
    current_time_min = parse_hhmm(DEFAULT_TW_START, DEFAULT_TW_START)
    previous_departure_min = solution.route_start_min or parse_hhmm(clients[0].time_window_start, DEFAULT_TW_START)
    previous_node = solution.sequence[0]

    for stop_number, node in enumerate(route_client_nodes, start=1):
        client = clients[node]
        distance_from_previous_km = distance_matrix[previous_node][node] / 1000
        travel_time_from_previous_min = travel_time_matrix[previous_node][node]
        expected_return_units = expected_returns_by_node[node]
        raw_arrival_min = previous_departure_min + travel_time_from_previous_min

        if node in solution.arrival_by_node:
            eta_min = solution.arrival_by_node[node]
        else:
            current_time_min += travel_time_from_previous_min
            eta_min = current_time_min
            current_time_min += client.service_min

        ordered_stops.append(
            {
                "stop_number": stop_number,
                "client_id": client.client_id,
                "client_name": client.client_name,
                "address": client.address,
                "town": client.town,
                "zone": client.zone,
                "zone_code": client.zone_code,
                "lat": round(client.lat, 6),
                "lon": round(client.lon, 6),
                "eta": format_hhmm(eta_min),
                "time_window_start": client.time_window_start,
                "time_window_end": client.time_window_end,
                "time_window_status": time_window_status(
                    client.time_window_start,
                    client.time_window_end,
                    eta_min,
                    raw_arrival_min,
                ),
                "distance_from_previous_km": round(distance_from_previous_km, 2),
                "travel_time_from_previous_min": travel_time_from_previous_min,
                "service_min": client.service_min,
                "original_order": client.original_order,
                "demand_units": json_number(client.demand_units),
                "total_proforma": client.total_proforma,
                "total_cash": client.total_cash,
                "delivery_units": delivery_units_by_node[node],
                "expected_return_units": expected_return_units,
                "return_confidence": return_confidence_by_node[node],
                "return_risk": return_risk(expected_return_units),
            }
        )
        previous_departure_min = eta_min + client.service_min
        previous_node = node

    load_simulation = build_load_simulation(ordered_stops)
    time_window_summary = build_time_window_summary(ordered_stops, solution.solver)
    zone_source = payload_zone_source(clients)
    extra_warnings = list(distance_provider_warnings or []) + build_zone_warnings(clients)
    warnings = build_warnings(
        load_simulation,
        time_window_summary,
        solution.solver,
        extra_warnings,
    )
    zone_sequence = build_zone_sequence(ordered_stops)
    zone_summary = build_zone_summary(ordered_stops)
    zone_transitions = count_zone_transitions(ordered_stops)

    return {
        **ROUTE_META,
        "solver": solution.solver,
        "total_distance_km": round(total_distance_m / 1000, 1),
        "estimated_time_min": int(estimated_time_min),
        "average_speed_kmh": AVERAGE_SPEED_KMH,
        "service_time_default_min": DEFAULT_SERVICE_MIN,
        "distance_provider": distance_provider,
        "distance_provider_note": distance_provider_note(distance_provider),
        "ordered_stops": ordered_stops,
        "time_window_summary": time_window_summary,
        "zone_sequence": zone_sequence,
        "zone_source": zone_source,
        "zone_summary": zone_summary,
        "zone_transitions": zone_transitions,
        "zone_explanation": zone_explanation(),
        "macro_zone_suggestions": build_macro_zone_suggestions(zone_summary),
        "reverse_logistics": {
            "known_total_delivery_units": KNOWN_TOTAL_DELIVERY_UNITS,
            "known_total_return_units": KNOWN_TOTAL_RETURN_UNITS,
            "known_total_delivery_weight_kg": KNOWN_TOTAL_DELIVERY_WEIGHT_KG,
            "known_total_return_weight_kg": KNOWN_TOTAL_RETURN_WEIGHT_KG,
            "per_client_returns": "estimated_not_confirmed",
            "strategy": "dynamic_buffer",
            "note": (
                "Route-level return totals are known from Hoja de Carga. Per-client returns are estimated "
                "from pallet share for simulation and should be replaced by historical client return data "
                "in a real pilot."
            ),
        },
        "load_simulation": load_simulation,
        "warnings": warnings,
        "baseline_comparison": {
            "baseline_distance_km": None,
            "optimized_distance_km": round(total_distance_m / 1000, 1),
            "distance_saving_km": None,
            "distance_saving_percent": None,
            "note": "Baseline will be filled when Persona 1 provides original route distance.",
        },
    }


def write_json(payload: Dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")


def generate_map(
    clients: Sequence[Client],
    solution: RouteSolution,
    map_output_path: Path,
    distance_provider: str,
) -> bool:
    try:
        import folium
    except ImportError:
        print("Folium is not installed; skipping map generation.")
        return False

    map_output_path.parent.mkdir(parents=True, exist_ok=True)
    route_points = [(clients[node].lat, clients[node].lon) for node in solution.sequence]

    route_map = folium.Map(location=[clients[0].lat, clients[0].lon], zoom_start=10, tiles="OpenStreetMap")
    provider_note = (
        "Optimization distances: OSRM road matrix. Map line is a visual stop-to-stop polyline."
        if distance_provider == "osrm"
        else "Optimization distances: Haversine straight-line matrix. Map line is a visual stop-to-stop polyline."
    )
    route_map.get_root().html.add_child(
        folium.Element(
            '<div style="position: fixed; bottom: 18px; left: 18px; z-index: 9999; '
            'background: white; border: 1px solid #888; border-radius: 4px; padding: 8px 10px; '
            'font: 12px Arial, sans-serif; box-shadow: 0 1px 4px rgba(0,0,0,.25);">'
            f"{provider_note}</div>"
        )
    )
    folium.Marker(
        [clients[0].lat, clients[0].lon],
        popup=f"{DEPOT_ID}: {clients[0].client_name}",
        tooltip=clients[0].client_name,
        icon=folium.Icon(color="red", icon="home", prefix="fa"),
    ).add_to(route_map)

    for stop_number, node in enumerate(solution.sequence[1:-1], start=1):
        client = clients[node]
        folium.Marker(
            [client.lat, client.lon],
            popup=f"{stop_number}. {client.client_name}<br>{client.address}<br>{client.town}<br>Zone: {client.zone}",
            tooltip=f"{stop_number}. {client.client_name}",
            icon=folium.DivIcon(
                html=(
                    '<div style="background:#155EEF;color:white;border:2px solid white;'
                    'border-radius:50%;width:28px;height:28px;line-height:24px;'
                    'text-align:center;font-size:12px;font-weight:700;'
                    'box-shadow:0 1px 4px rgba(0,0,0,.35);">'
                    f"{stop_number}</div>"
                )
            ),
        ).add_to(route_map)

    folium.PolyLine(route_points, color="#155EEF", weight=4, opacity=0.8).add_to(route_map)
    route_map.fit_bounds(route_points, padding=(24, 24))
    route_map.save(str(map_output_path))
    return True


def run_optimizer(
    input_path: Path,
    output_path: Path,
    map_output_path: Path,
    distance_provider: str = "haversine",
) -> Dict[str, object]:
    clients = load_clients(input_path)
    distance_data = build_distance_data(clients, distance_provider)
    solution = optimize_route(clients, distance_data.distance_matrix, distance_data.travel_time_matrix)
    solution = apply_return_aware_soft_optimization(
        clients,
        distance_data.distance_matrix,
        distance_data.travel_time_matrix,
        solution,
    )
    solution = apply_zone_aware_soft_optimization(
        clients,
        distance_data.distance_matrix,
        distance_data.travel_time_matrix,
        solution,
    )
    payload = build_output_payload(
        clients,
        distance_data.distance_matrix,
        distance_data.travel_time_matrix,
        solution,
        distance_data.provider,
        distance_data.warnings,
    )

    write_json(payload, output_path)
    map_created = generate_map(clients, solution, map_output_path, distance_data.provider)

    print(f"Solver: {payload['solver']}")
    print(f"Distance provider: {payload['distance_provider']}")
    print(f"Stops optimized: {len(payload['ordered_stops'])}")
    print(f"Total distance: {payload['total_distance_km']} km")
    print(f"Estimated time: {payload['estimated_time_min']} min")
    print(f"JSON written: {output_path}")
    print(f"Map {'written' if map_created else 'skipped'}: {map_output_path}")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize single-truck DR0027 route order.")
    parser.add_argument("--input", required=True, type=Path, help="CSV file matching the Persona 1 input contract.")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path.")
    parser.add_argument(
        "--map-output",
        type=Path,
        default=Path("outputs/ruta_optimitzada_map.html"),
        help="Optional Folium HTML map output path.",
    )
    parser.add_argument(
        "--distance-provider",
        choices=["haversine", "osrm"],
        default="haversine",
        help="Distance matrix provider. OSRM is optional and falls back to Haversine on failure.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_optimizer(args.input, args.output, args.map_output, args.distance_provider)


if __name__ == "__main__":
    main()
