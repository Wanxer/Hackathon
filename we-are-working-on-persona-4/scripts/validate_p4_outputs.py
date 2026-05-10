from __future__ import annotations

import json
from pathlib import Path
import py_compile
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.p4_core import EXPECTED_DATE, EXPECTED_ROUTE_ID, ZONE_IDS, ZONE_CAPACITY, MAX_TOTAL_PALLETS, load_data, p4_paths
from scripts.generate_reports import clean_pdf_text


FORBIDDEN_REPORT_TEXT = [
    "<b>",
    "</b>",
    "<br",
    "br/>",
    "FRONT / CABIN",
    "REAR",
    "Driver Loading Plan",
    "Route comparison",
    "Truck layout",
    "Ordered stops",
    "Open side curtain",
    "&nbsp;",
]

REQUIRED_REPORT_TEXT = [
    "Pla de càrrega i repartiment",
    "Ruta DR0051",
    "2026-02-05",
    "Transport 11443257",
    "Repartidor 855190",
    "DAVANT / CABINA",
    "DARRERE",
    "Distància original",
    "Distància optimitzada",
    "Reducció de distància",
]


def contains_old_route(path: Path) -> bool:
    if not path.exists() or path.is_dir():
        return False
    marker = ("DR" + "0027").encode()
    return marker in path.read_bytes()


def read_text_if_exists(path: Path) -> str:
    if not path.exists() or path.is_dir():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def read_pdf_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def missing_snippets(text: str, snippets: Iterable[str]) -> list[str]:
    return [snippet for snippet in snippets if snippet not in text]


def present_snippets(text: str, snippets: Iterable[str]) -> list[str]:
    return [snippet for snippet in snippets if snippet in text]


def main() -> None:
    paths = p4_paths(ROOT)
    data = load_data(ROOT)
    layout = data.get("layout") or {}
    report_text = paths["report_html"].read_text(encoding="utf-8") if paths["report_html"].exists() else ""
    driver_text = read_text_if_exists(paths["driver_html"])
    pdf_text = "\n".join([read_pdf_text(paths["report_pdf"]), read_pdf_text(paths["driver_pdf"])])
    combined_report_text = "\n".join([report_text, driver_text, pdf_text])

    py_compile.compile(str(ROOT / "app.py"), doraise=True)
    py_compile.compile(str(ROOT / "scripts" / "generate_reports.py"), doraise=True)
    py_compile.compile(str(ROOT / "scripts" / "validate_p4_outputs.py"), doraise=True)

    enough_map_data = paths["baseline_geometry"].exists() and paths["clients_geo"].exists() and paths["optimized_route"].exists()
    final_outputs = [
        paths["report_html"],
        paths["report_pdf"],
        paths["driver_html"],
        paths["driver_pdf"],
        paths["final_map"],
        paths["layout_html"],
    ]
    checks = {
        "app_py_exists": (ROOT / "app.py").exists(),
        "report_html_exists": paths["report_html"].exists(),
        "report_pdf_exists": paths["report_pdf"].exists(),
        "driver_html_exists": paths["driver_html"].exists(),
        "driver_pdf_exists": paths["driver_pdf"].exists(),
        "final_route_comparison_map_exists_if_possible": paths["final_map"].exists() if enough_map_data else True,
        "dashboard_syntax_ok": True,
        "route_in_final_report": EXPECTED_ROUTE_ID in report_text,
        "date_in_final_report": EXPECTED_DATE in report_text,
        "transport_in_final_report": "Transport 11443257" in combined_report_text,
        "driver_in_final_report": "Repartidor 855190" in combined_report_text,
        "layout_has_6_zones": len(layout.get("zones", [])) == 6,
        "zones_exact": [z.get("zone_id") for z in layout.get("zones", [])] == ZONE_IDS,
        "no_old_route_in_final_outputs": not any(contains_old_route(path) for path in final_outputs),
        "no_forbidden_report_text": not present_snippets(combined_report_text, FORBIDDEN_REPORT_TEXT),
        "required_catalan_report_text_present": not missing_snippets(combined_report_text, REQUIRED_REPORT_TEXT),
        "clean_pdf_text_removes_html": clean_pdf_text("<b>Davant</b><br />A&nbsp;&amp;&lt;x&gt;") == "Davant\nA &<x>",
    }

    # Physical pallet-slot constraint checks
    zones_data = layout.get("zones", [])

    # Every zone must have capacity_pallets = 1.0
    checks["zone_capacity_is_1"] = all(
        z.get("capacity_pallets") == ZONE_CAPACITY for z in zones_data
    )

    # Every zone must have used_pallets <= 1.0
    checks["zone_usage_within_capacity"] = all(
        z.get("used_pallets", 0) <= ZONE_CAPACITY + 0.001 for z in zones_data
    )

    # Total used_pallets <= 6.0
    total_used = sum(z.get("used_pallets", 0) for z in zones_data)
    checks["total_used_within_6"] = total_used <= MAX_TOTAL_PALLETS + 0.001

    # If total demand > 6.0, overflow must be reported
    summary = layout.get("summary", {})
    total_demand = summary.get("total_demand_pallets", total_used)
    if total_demand > MAX_TOTAL_PALLETS + 0.001:
        overflow_section = layout.get("overflow")
        checks["overflow_reported_if_needed"] = (
            summary.get("has_overflow", False) and overflow_section is not None
        )
    else:
        checks["overflow_reported_if_needed"] = True

    # No zone should show estimated_pallet_usage > 1.0 (old broken format)
    checks["no_zone_over_1_pallet"] = all(
        z.get("estimated_pallet_usage", z.get("used_pallets", 0)) <= ZONE_CAPACITY + 0.001
        for z in zones_data
    )

    failed = [key for key, value in checks.items() if not value]
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    forbidden_found = present_snippets(combined_report_text, FORBIDDEN_REPORT_TEXT)
    required_missing = missing_snippets(combined_report_text, REQUIRED_REPORT_TEXT)
    if forbidden_found:
        print(json.dumps({"forbidden_found": forbidden_found}, ensure_ascii=False, indent=2))
    if required_missing:
        print(json.dumps({"required_missing": required_missing}, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(f"P4 validation failed: {failed}")


if __name__ == "__main__":
    main()
