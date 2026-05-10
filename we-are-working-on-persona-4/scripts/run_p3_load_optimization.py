"""
run_p3_load_optimization.py
===========================
Reads the raw layout_camio.json (which may have zones with >1.00 pallet),
applies the physical pallet-slot constraint (max 1.00 per zone), and
writes the corrected layout + summary + HTML.

Usage:
    python scripts/run_p3_load_optimization.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.p4_core import (
    build_layout_summary,
    build_physical_layout,
    generate_layout_html,
    p4_paths,
    read_json,
    ensure_output_dirs,
    ZONE_CAPACITY,
    MAX_TOTAL_PALLETS,
)


def main() -> None:
    paths = p4_paths(ROOT)
    ensure_output_dirs(ROOT)

    print("=" * 60)
    print("  P3 Load Optimization — Physical Pallet-Slot Constraint")
    print("=" * 60)

    # Read raw layout
    raw_layout = read_json(paths["layout"])
    if raw_layout is None:
        print(f"ERROR: layout_camio.json not found at {paths['layout']}")
        sys.exit(1)

    # Show current state
    raw_zones = raw_layout.get("zones", [])
    print(f"\nRaw layout: {len(raw_zones)} zones")
    for z in raw_zones:
        zid = z.get("zone_id", "?")
        usage = z.get("estimated_pallet_usage", z.get("used_pallets", 0))
        print(f"  {zid}: {usage:.4f} palets (RAW)")

    raw_total = sum(
        z.get("estimated_pallet_usage", z.get("used_pallets", 0))
        for z in raw_zones
    )
    print(f"  TOTAL: {raw_total:.4f} palets")
    print(f"  Capacitat física: {MAX_TOTAL_PALLETS:.0f} posicions x {ZONE_CAPACITY:.0f} palet")

    # Apply physical constraint
    print("\nAplicant restricció física (1 zona = 1 palet)...")
    physical_layout = build_physical_layout(raw_layout)

    # Show result
    print("\nResultat:")
    for z in physical_layout.get("zones", []):
        zid = z["zone_id"]
        used = z["used_pallets"]
        cap = z["capacity_pallets"]
        free = z["free_pallets"]
        n_clients = len(z.get("assigned_clients", []))
        n_products = len(z.get("assigned_products", []))
        status = "✅" if used <= cap else "❌ EXCEDIT!"
        print(
            f"  {zid}: {used:.2f} / {cap:.2f} palet  "
            f"(lliure: {free:.2f})  "
            f"clients: {n_clients}, productes: {n_products}  {status}"
        )

    summary = physical_layout.get("summary", {})
    print(f"\n  Total usat: {summary.get('total_used_pallets', 0):.2f} / {MAX_TOTAL_PALLETS:.0f}")
    print(f"  Demanda total: {summary.get('total_demand_pallets', 0):.2f}")

    overflow = physical_layout.get("overflow")
    if overflow:
        print(f"\n  ⚠️  OVERFLOW: {overflow.get('overflow_pallets', 0):.2f} palets en excés")
        print(f"  {overflow.get('overflow_reason', '')}")

    # Write outputs
    layout_path = paths["layout"]
    layout_path.write_text(
        json.dumps(physical_layout, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ layout_camio.json escrit: {layout_path}")

    summary_data = build_layout_summary(physical_layout)
    summary_path = paths["layout_summary"]
    summary_path.write_text(
        json.dumps(summary_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ layout_camio_resum.json escrit: {summary_path}")

    html_path = paths["layout_html"]
    generate_layout_html(physical_layout, html_path)
    print(f"✅ layout_camio.html escrit: {html_path}")

    # Final validation
    all_ok = True
    for z in physical_layout.get("zones", []):
        if z["used_pallets"] > ZONE_CAPACITY + 0.001:
            print(f"❌ VALIDACIÓ FALLIDA: {z['zone_id']} té {z['used_pallets']:.4f} > {ZONE_CAPACITY}")
            all_ok = False
    if all_ok:
        print("\n✅ Totes les zones respecten la capacitat física (≤ 1.00 palet per zona)")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
