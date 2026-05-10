# Damm Smart Truck — Interhack BCN 2026

Repte: optimitzar conjuntament la ruta de repartiment i la càrrega d'un camió de DDI Mollet.

## Estructura
- `data/raw/` — fitxers Excel originals (no modificar)
- `data/processed/` — datasets nets generats
- `notebooks/` — exploració i prototipat
- `src/` — codi del prototip final

## Cas d'ús
Transporte 11443257 — Ruta DR0051 zona Vic — 24 clients — 5/2/2026

## P1 — Data and baseline

Persona 1 rebuilds the real DR0051 dataset from the extracted Damm source data for
2026-02-05:

- Transport: `11443257`
- Route: `DR0051`
- Driver/Repartidor: `855190`
- Real clients: `24`
- Order lines: `196`
- Depot: `DDI Mollet`

The generated client dataset starts from `data/processed/cas_us_clients.csv`.
Geocoding uses `geopy` + Nominatim with rate limiting. If Nominatim is unavailable
or cannot safely resolve a real client address, a manual reviewed fallback
coordinate is used only for that same real client and marked as `MANUAL_REVIEWED`.

Baseline distance and driving time are calculated with OSRM road routing. Unloading
time is assumed to be `15` minutes per client, so this route has `6.0` hours of
estimated unloading time.

Deliverables generated:

- `data/processed/cas_us_linies.csv`
- `data/processed/cas_us_clients.csv`
- `data/processed/clients_geo.csv`
- `data/processed/baseline_real.json`
- `data/processed/baseline_route_geometry.geojson`
- `outputs/mapa_baseline.html`

Rebuild and validate:

```bash
python3 scripts/p1_pipeline.py
python3 scripts/validate_p1_deliverables.py
```

If Nominatim is rate-limited during a demo run, rebuild with the reviewed fallback
coordinates directly:

```bash
python3 scripts/p1_pipeline.py --skip-nominatim
```
