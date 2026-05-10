# Damm Smart Planner — P4

## P4 — Dashboard final i informes

P4 és la capa final de presentació del projecte Damm Smart Planner. Integra
les dades reals de ruta, l'anàlisi de la ruta original, l'ordre optimitzat i
la distribució de càrrega del camió en un dashboard i un paquet de reports.

Context correcte de la ruta:

- Ruta: `DR0051`
- Data: `2026-02-05`
- Transport: `11443257`
- Repartidor: `855190`
- Clients: `24`
- Depot: `DDI Mollet`, client_id `0`

Fitxers d'entrada requerits:

- `data/processed/clients_geo.csv`
- `data/processed/cas_us_clients.csv`
- `data/processed/cas_us_linies.csv`
- `data/processed/baseline_real.json`
- `data/processed/baseline_route_geometry.geojson`
- `data/processed/ruta_optimitzada.json`
- `data/processed/layout_camio.json`
- `data/processed/layout_camio_resum.json`
- `outputs/layout_camio.html`
- `outputs/mapa_baseline.html`

Entrada opcional:

- `outputs/ruta_optimitzada_map.html`

Executar el dashboard:

```bash
streamlit run app.py
```

Generar reports:

```bash
python3 scripts/generate_reports.py
```

Validar sortides:

```bash
python3 scripts/validate_p4_outputs.py
```

Fitxers de sortida generats:

- `outputs/final_route_comparison_map.html`
- `reports/damm_smart_planner_report.html`
- `reports/damm_smart_planner_report.pdf`
- `reports/driver_loading_plan.html`
- `reports/driver_loading_plan.pdf`

Què mostra el dashboard:

- Resum executiu de la ruta real DR0051.
- Comparativa entre ruta original i ruta optimitzada.
- Mapes de ruta original, optimitzada i comparativa final.
- Distribució del camió en sis zones amb usos, clients, productes i explicacions.
- Explicació clara de les regles de càrrega per al jurat i l'equip operatiu.
- Validacions de qualitat de dades i botons de descàrrega dels artefactes.

P4 és de només lectura respecte a les dades processades de P1/P2/P3. Només
escriu actius finals de presentació dins `outputs/` i `reports/`.
