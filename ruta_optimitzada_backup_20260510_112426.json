"""
05_executar_i_actualitzar_2.py
==============================
Pipeline simple: a partir del ruta_optimitzada.json (un camió amb les
seves parades), genera els dos PDFs:

  pdf_mosso.pdf      → com omplir els palets del camió
  pdf_repartidor.pdf → què entregar a cada client en l'ordre de visita

NO modifica cap fitxer d'entrada. El ruta_optimitzada.json queda
intacte: pots tornar a executar quantes vegades vulguis i obtindràs
sempre els mateixos PDFs.

Execució:
  python 05_executar_i_actualitzar_2.py

Inputs (no es modifiquen):
  data/ruta_optimitzada.json
  data/Hackaton.xlsx
  data/ZM040.xlsx

Outputs:
  layout_camio.json
  pdf_mosso.pdf
  pdf_repartidor.pdf
"""

import os
import sys
import json
from datetime import datetime
import importlib.util
import warnings
warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────
# CONFIGURACIÓ
# ─────────────────────────────────────────────────────

DIR_BASE       = os.path.dirname(os.path.abspath(__file__))
PATH_RUTA      = os.path.join(DIR_BASE, "data", "ruta_optimitzada.json")
SCRIPT_CARREGA = os.path.join(DIR_BASE, "03_assignar_carrega.py")
SCRIPT_PDFS    = os.path.join(DIR_BASE, "04_generar_pdfs.py")


# ─────────────────────────────────────────────────────
# UTILITATS
# ─────────────────────────────────────────────────────

def carregar_script(path):
    """Carrega un script Python com a mòdul per cridar-ne el main()."""
    spec  = importlib.util.spec_from_file_location("modul", path)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


# ─────────────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  DAMM ROUTES — Generació de PDFs")
    print("=" * 55)
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print()

    # ── Verificar que existeixen els fitxers necessaris ──
    for path in (PATH_RUTA, SCRIPT_CARREGA, SCRIPT_PDFS):
        if not os.path.exists(path):
            print(f"❌ No trobo: {path}")
            sys.exit(1)

    # ── Llegir el JSON i comprovar que té parades ──
    with open(PATH_RUTA, encoding="utf-8") as f:
        ruta = json.load(f)

    stops    = ruta.get("ordered_stops", [])
    route_id = ruta.get("route_id") or ruta.get("ruta")
    date_iso = ruta.get("date")

    print(f"  Ruta     : {route_id}")
    print(f"  Data     : {date_iso}")
    print(f"  Parades  : {len(stops)}")

    if not stops:
        print()
        print("❌ El ruta_optimitzada.json no té parades.")
        print("   Restaura'l amb un fitxer que en tingui i torna a executar.")
        sys.exit(1)

    client_ids = [
        str(s.get("client_id", "")).split(".")[0]
        for s in stops if s.get("client_id")
    ]

    # Convertir data ISO ('2026-05-08') a format DD/MM/YYYY
    data_03 = None
    if date_iso:
        try:
            data_03 = datetime.fromisoformat(date_iso).strftime("%d/%m/%Y")
        except ValueError:
            data_03 = date_iso

    print()

    # ── PAS 1: Assignar càrrega als palets ──
    print("─" * 55)
    print("PAS 1 — Assignació de càrrega (03_assignar_carrega.py)")
    print("─" * 55)
    modul_carrega = carregar_script(SCRIPT_CARREGA)
    if route_id:
        modul_carrega.RUTA_OBJECTIU = route_id
    if data_03:
        modul_carrega.DATA_OBJECTIU = data_03
    if client_ids:
        modul_carrega.CLIENT_IDS_OBJECTIU = client_ids
    modul_carrega.main()

    # ── PAS 2: Generar els PDFs ──
    print()
    print("─" * 55)
    print("PAS 2 — Generació de PDFs (04_generar_pdfs.py)")
    print("─" * 55)
    modul_pdfs = carregar_script(SCRIPT_PDFS)
    modul_pdfs.main()

    # ── Resum final ──
    print()
    print("=" * 55)
    print("✅ Pipeline completat!")
    print(f"   layout_camio.json   → assignació de palets")
    print(f"   pdf_mosso.pdf       → PDF mosso de magatzem")
    print(f"   pdf_repartidor.pdf  → PDF repartidor")
    print("   ruta_optimitzada.json no s'ha tocat.")
    print("=" * 55)


if __name__ == "__main__":
    main()
