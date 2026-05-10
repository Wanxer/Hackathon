"""
validate_p3_outputs.py
======================
Validates that layout_camio.json respects physical pallet-slot constraints:
  - Exactly 6 zones: L1, L2, L3, R1, R2, R3
  - Each zone has capacity_pallets = 1.0
  - Each zone has used_pallets <= 1.0
  - Total used_pallets <= 6.0
  - Overflow is explicitly reported when demand > 6.0

Usage:
    python scripts/validate_p3_outputs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.p4_core import (
    ZONE_IDS,
    ZONE_CAPACITY,
    MAX_TOTAL_PALLETS,
    p4_paths,
    read_json,
)


def main() -> None:
    paths = p4_paths(ROOT)
    layout = read_json(paths["layout"])

    print("=" * 60)
    print("  Validació P3 — Restricció física de palets")
    print("=" * 60)

    checks: dict[str, bool] = {}
    messages: list[str] = []

    if layout is None:
        print(f"ERROR: No es troba layout_camio.json a {paths['layout']}")
        sys.exit(1)

    zones = layout.get("zones", [])
    zone_ids = [z.get("zone_id") for z in zones]

    # Check 1: Exactly 6 zones
    checks["exactly_6_zones"] = len(zones) == 6
    if not checks["exactly_6_zones"]:
        messages.append(f"Esperats 6 zones, trobats {len(zones)}")

    # Check 2: Zone IDs are correct
    checks["zone_ids_correct"] = zone_ids == ZONE_IDS
    if not checks["zone_ids_correct"]:
        messages.append(f"Zone IDs esperats: {ZONE_IDS}, trobats: {zone_ids}")

    # Check 3: Every zone has capacity_pallets = 1.0
    checks["all_zones_capacity_1"] = all(
        z.get("capacity_pallets") == ZONE_CAPACITY for z in zones
    )
    if not checks["all_zones_capacity_1"]:
        for z in zones:
            cap = z.get("capacity_pallets")
            if cap != ZONE_CAPACITY:
                messages.append(
                    f"  {z['zone_id']}: capacity_pallets = {cap} (esperat {ZONE_CAPACITY})"
                )

    # Check 4: Every zone has used_pallets <= 1.0
    checks["all_zones_within_capacity"] = all(
        z.get("used_pallets", 0) <= ZONE_CAPACITY + 0.001 for z in zones
    )
    if not checks["all_zones_within_capacity"]:
        for z in zones:
            used = z.get("used_pallets", 0)
            if used > ZONE_CAPACITY + 0.001:
                messages.append(
                    f"  ❌ {z['zone_id']}: used_pallets = {used:.4f} > {ZONE_CAPACITY}"
                )

    # Check 5: Total used <= 6.0
    total_used = sum(z.get("used_pallets", 0) for z in zones)
    checks["total_within_6"] = total_used <= MAX_TOTAL_PALLETS + 0.001
    if not checks["total_within_6"]:
        messages.append(
            f"Total used_pallets = {total_used:.4f} > {MAX_TOTAL_PALLETS}"
        )

    # Check 6: Overflow reported when demand > 6.0
    summary = layout.get("summary", {})
    total_demand = summary.get("total_demand_pallets", 0)
    has_overflow_flag = summary.get("has_overflow", False)
    overflow_section = layout.get("overflow")
    if total_demand > MAX_TOTAL_PALLETS + 0.001:
        checks["overflow_reported"] = (
            has_overflow_flag and overflow_section is not None
        )
        if not checks["overflow_reported"]:
            messages.append(
                f"Demanda = {total_demand:.2f} > {MAX_TOTAL_PALLETS} "
                "però no hi ha secció overflow"
            )
    else:
        checks["overflow_reported"] = True  # No overflow needed

    # Check 7: No zone shows estimated_pallet_usage > 1.0 (old format)
    checks["no_old_format_over_1"] = True
    for z in zones:
        old_usage = z.get("estimated_pallet_usage", None)
        if old_usage is not None and old_usage > ZONE_CAPACITY + 0.001:
            checks["no_old_format_over_1"] = False
            messages.append(
                f"  {z['zone_id']}: estimated_pallet_usage = {old_usage} "
                "(format antic > 1.0)"
            )

    # Print results
    print("\nResultats:")
    for z in zones:
        used = z.get("used_pallets", 0)
        cap = z.get("capacity_pallets", "?")
        status = "✅" if used <= ZONE_CAPACITY + 0.001 else "❌"
        print(f"  {z['zone_id']}: {used:.2f} / {cap} palet  {status}")

    print(f"\n  Total: {total_used:.2f} / {MAX_TOTAL_PALLETS:.0f}")
    if total_demand > MAX_TOTAL_PALLETS:
        print(f"  Demanda: {total_demand:.2f} (excés: {total_demand - MAX_TOTAL_PALLETS:.2f})")
    if overflow_section:
        print(f"  Overflow: {overflow_section.get('overflow_pallets', 0):.2f} palets")
        print(f"  Raó: {overflow_section.get('overflow_reason', 'N/A')}")

    print(f"\nChecks:")
    print(json.dumps(checks, indent=2))

    if messages:
        print(f"\nProblemes:")
        for msg in messages:
            print(f"  {msg}")

    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise SystemExit(f"P3 validation FAILED: {failed}")
    else:
        print("\n✅ Totes les validacions han passat!")


if __name__ == "__main__":
    main()
