# Persona 2 - Route Optimizer

This folder contains the route optimization slice for the Damm SmartTruck Optimizer hackathon project.

Scope is deliberately limited to a single-truck route ordering problem:

- one depot
- one truck
- multiple delivery clients
- Haversine distance matrix
- optional OSRM road-distance matrix
- estimated travel time at 35 km/h
- optional time windows
- service time per stop
- OR-Tools solver with nearest-neighbor fallback
- estimated reverse logistics fields for capacity simulation

It does not implement frontend, Streamlit, database, FastAPI, Gemini, truck loading, deployment, or authentication.

## Files

- `src/route_optimizer.py`: CLI and optimization logic.
- `data/processed/clients_geo.csv`: Persona 1 geocoded route input for DR0027 when available.
- `data/mock/clients_mock.csv`: realistic mock clients for DR0027 around Sant Julià de Vilatorta, Calldetenes, and Folgueroles.
- `data/processed/ruta_optimitzada.json`: generated optimized route JSON.
- `outputs/ruta_optimitzada_map.html`: generated Folium map when `folium` is installed.
- `requirements_route.txt`: route optimizer dependencies.

## Input CSV Contract

The preferred Persona 1 input is:

```bash
python src/route_optimizer.py --input data/processed/clients_geo.csv --output data/processed/ruta_optimitzada.json
```

`clients_geo.csv` is expected to contain:

```text
client_id
nom
poblacio
lat
lon
palets
finestra_inici
finestra_fi
geo_status
```

If the file includes Damm transport-zone columns, the optimizer prefers the human zone name (`ZonaTransp.1` or `ZonaTranspNombre`, for example `CENTRE`, `UNIVERSITA`, `AFORES`, `ESTADI`, `ST.JULIA`) and keeps the official transport-zone code separately as `zone_code` (`ZonaTransp`, for example `DD13100053`). Zone analytics use this transport-zone name, not the town. If no transport-zone column is present, it uses `poblacio` as a fallback; if that is also missing, it uses `UNKNOWN`.

The optimizer normalizes this internally as:

- `client_name = nom`
- `town = poblacio`
- `demand_units = palets`
- `time_window_start = finestra_inici`
- `time_window_end = finestra_fi`
- `zone = ZonaTransp.1 / ZonaTranspNombre when available`
- `zone_code = ZonaTransp when available`

`poblacio` / `town` remains in every stop for display and location context only. It is not used for `zone_sequence`, `zone_summary`, `zone_transitions`, or `macro_zone_suggestions` when transport-zone data is available. This matters for towns like `VIC`, where different clients can belong to zones such as `CENTRE`, `UNIVERSITA`, `AFORES`, or `ESTADI`.

It also keeps the older mock CSV format working:

```text
client_id
client_name
address
town
lat
lon
demand_units
time_window_start
time_window_end
service_min
original_order
total_proforma
total_cash
```

The optimizer also accepts optional columns:

```text
delivery_units
expected_return_units
return_confidence
```

The route is optimized from and back to DDI Mollet. The depot may be included as `client_id=0` or `client_id=DEPOT`. If it is missing, the optimizer automatically inserts:

- name: DDI Mollet
- address: C/Molí de Can Bassa, Nau Damm 1, Mollet del Vallès
- lat: 41.5427
- lon: 2.2135

Time windows use `HH:MM`. Missing time windows default to `08:00-18:00`.
Missing service time defaults to `8` minutes for clients and `0` minutes for the depot.

Demand fields are loaded as metadata and as a fallback for estimated unit distribution. Capacity and truck loading remain outside Persona 2.

## Reverse Logistics

For load `11764300` / route `DR0027`, the route-level Hoja de Carga totals are known:

- total delivery units: `837`
- total return units: `259`
- total delivery weight: `4719.120 kg`
- total return weight: `2094.276 kg`

Per-client return quantities are not confirmed in the source data yet. When `expected_return_units` is missing, the optimizer estimates per-stop returns so the ordered stops sum exactly to `259` units. It distributes returns proportionally by pallet share from `palets` / normalized `demand_units`, and marks the stop-level `return_confidence` as `estimated_from_route_total_and_palets`.

When `delivery_units` is missing, the optimizer estimates delivery units so the ordered stops sum exactly to `837` units. It distributes delivery units by pallet share when possible, otherwise evenly across stops.

These reverse logistics values are used for dynamic capacity simulation and soft route evaluation only. Distance and delivery time windows remain the main route constraints, and returns never override hard time windows. In a real pilot, per-client returns should be replaced with historical return behavior or confirmed return assignments.

## Install

```bash
pip install -r requirements_route.txt
```

If `pip` is not on PATH, use:

```bash
python3 -m pip install -r requirements_route.txt
```

If OR-Tools is not installed, the script still produces a JSON using the nearest-neighbor fallback.
If Folium is not installed, the JSON is still produced and map generation is skipped gracefully.

## Distance Provider

By default, the optimizer uses Haversine straight-line distances. This is fast, offline-friendly, and stable for demos:

```bash
python3 src/route_optimizer.py --input data/processed/clients_geo.csv --output data/processed/ruta_optimitzada.json --distance-provider haversine
```

Optional OSRM mode uses the public OSRM demo server to request road distances and durations:

```bash
python3 src/route_optimizer.py --input data/processed/clients_geo.csv --output data/processed/ruta_optimitzada.json --distance-provider osrm
```

OSRM mode calls `https://router.project-osrm.org` with the Table API. The public demo server is useful for hackathon validation, but it is not guaranteed for production availability, rate limits, or latency. If OSRM fails, times out, returns invalid matrices, or has missing cells, the optimizer prints a warning, records it in the JSON, and automatically falls back to Haversine.

The Folium map still draws a stop-to-stop polyline for visualization. The JSON fields `distance_provider` and `distance_provider_note` indicate whether optimization used OSRM road matrices or Haversine fallback.

## Run With Mock Data

```bash
python src/route_optimizer.py --input data/mock/clients_mock.csv --output data/processed/ruta_optimitzada.json
```

On machines where `python` is not available, use:

```bash
python3 src/route_optimizer.py --input data/mock/clients_mock.csv --output data/processed/ruta_optimitzada.json
```

## Run With Persona 1 Output

```bash
python src/route_optimizer.py --input data/processed/clients_geo.csv --output data/processed/ruta_optimitzada.json
```

## Solver Behavior

The route optimizer tries:

1. `ortools_vrptw`: OR-Tools with time windows.
2. `ortools_tsp`: OR-Tools without time windows if the VRPTW is infeasible.
3. `nearest_neighbor_fallback`: deterministic fallback if OR-Tools is unavailable or fails.

This keeps the hackathon demo reliable even when optional dependencies or strict time windows cause trouble.

## Output JSON

The output is stable for Persona 3 and Persona 4 consumers:

- route metadata for DR0027 / load `11764300`
- solver name
- total optimized distance
- estimated time
- ordered client stops with ETA, time-window fields/status, and per-leg distance/time
- top-level `time_window_summary` showing whether VRPTW enforced windows and stop status counts
- per-stop `zone` and `zone_code`, top-level `zone_source`, plus `zone_sequence`, `zone_summary`, `zone_transitions`, `zone_explanation`, and `macro_zone_suggestions`
- `delivery_units`, `expected_return_units`, `return_confidence`, and `return_risk` per stop
- route-level `reverse_logistics` metadata
- `load_simulation` after each optimized stop
- non-blocking `warnings` when reverse logistics buffer risk is relevant
- baseline comparison placeholder

Baseline distance is intentionally left as `null` until Persona 1 provides an original route distance.
