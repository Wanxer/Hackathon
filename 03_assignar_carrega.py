"""
04_generar_pdfs.py
==================
Genera dos PDFs a partir del layout_camio.json i ruta_optimitzada.json:

  pdf_mosso.pdf      → Com omplir cada palet del camió (A4 apaïsat)
  pdf_repartidor.pdf → Què entregar a cada client (A4 vertical)

En el PDF del repartidor, els productes apareixen en ordre de recollida:
  1r → Lleugers i secs (a dalt del palet, s'agafen primer)
  ...
  Últim → Barrils i CO2 (a baix del palet, s'agafen últims)

Inputs:
  data/layout_camio.json       (generat per 03_assignar_carrega.py)
  data/ruta_optimitzada.json   (generat per P2)
  data/Hackaton.xlsx           (per als productes dels clients nous)

Outputs:
  pdf_mosso.pdf
  pdf_repartidor.pdf

Execució:
  pip install reportlab
  python 04_generar_pdfs.py
"""

import json
import os
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ─────────────────────────────────────────────────────
# CONFIGURACIÓ
# ─────────────────────────────────────────────────────

PATH_LAYOUT  = "data/layout_camio.json"
PATH_RUTA    = "data/ruta_optimitzada.json"
PATH_HACKATON = "data/Hackaton.xlsx"
PATH_OUT_MOSSO = "pdf_mosso.pdf"
PATH_OUT_REP   = "pdf_repartidor.pdf"

# Colors per capa de pes
CAPA_COLORS = {
    1: colors.HexColor("#8B6355"),   # Marró  = barrils/CO2 (BASE)
    2: colors.HexColor("#4CAF79"),   # Verd   = cervesa vidre
    3: colors.HexColor("#5B9BD5"),   # Blau   = aigues/refrescos
    4: colors.HexColor("#F0C040"),   # Groc   = secs/lleugers
}
CAPA_NOMS = {
    1: "BASE — Barrils i CO2 (posar a baix)",
    2: "MIG-BAIX — Cervesa vidre",
    3: "MIG — Aigues i refrescos",
    4: "DALT — Secs i lleugers (mai res pesant a sobre)",
}

# Colors generals
COLOR_HEADER    = colors.HexColor("#2c3e50")
COLOR_BLUE      = colors.HexColor("#3498db")
COLOR_GREEN     = colors.HexColor("#27ae60")
COLOR_ORANGE    = colors.HexColor("#e67e22")
COLOR_LIGHT     = colors.HexColor("#f0f4f8")
COLOR_LONA      = colors.HexColor("#d6eaf8")   # Blau clar per banda lona
COLOR_INTERIOR  = colors.HexColor("#f7f7f7")   # Gris per banda interior
COLOR_WHITE     = colors.white
COLOR_BLACK     = colors.black


# ─────────────────────────────────────────────────────
# 1. CÀRREGA DE DADES
# ─────────────────────────────────────────────────────

def carregar_dades():
    print("Carregant dades...")

    # Layout del camió
    for p in [PATH_LAYOUT, "layout_camio.json"]:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                layout = json.load(f)
            break
    else:
        raise FileNotFoundError("No trobo layout_camio.json")

    # Ruta de P2
    ruta_p2 = None
    for p in [PATH_RUTA, "ruta_optimitzada.json"]:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                ruta_p2 = json.load(f)
            break

    # Hackaton per a clients nous
    df_hack = None
    for p in [PATH_HACKATON, "Hackaton.xlsx"]:
        try:
            df_hack = pd.read_excel(p, sheet_name="Detalle entrega")
            break
        except Exception:
            continue

    # Construir mapa client_id → productes (de l'última comanda disponible)
    id_to_prods = {}
    if df_hack is not None and ruta_p2:
        cids_json = [str(stop.get("client_id", "")).split(".")[0] for stop in ruta_p2.get("ordered_stops", [])]
        mask  = df_hack["Material"].str.startswith(("3ENV","CJ"), na=False)
        df_r  = df_hack[~mask].copy()
        
        # Filtrem pels clients que estan a la ruta optimitzada, independentment de quina ruta digui l'Excel
        mask_clients = df_r["Destinatario mcía..1"].astype(str).str.split(".").str[0].isin(cids_json)
        df_r = df_r[mask_clients]
        
        for cid, grup in df_r.groupby("Destinatario mcía..1"):
            ultima = grup.sort_values("FECHA", ascending=False)
            data_u = ultima["FECHA"].iloc[0]
            g_u    = grup[grup["FECHA"] == data_u]
            clau_str = str(cid).split(".")[0]
            id_to_prods[clau_str] = list(zip(
                g_u["Material"].tolist(),
                g_u["Denominación"].tolist(),
                g_u["Cantidad entrega"].tolist(),
                g_u["Un.medida venta"].tolist(),
            ))

    print(f"  Palets: {len(layout['palets'])}")
    print(f"  Parades JSON: {len(ruta_p2.get('ordered_stops', [])) if ruta_p2 else 0}")
    print(f"  Clients al Hackaton: {len(id_to_prods)}")
    return layout, ruta_p2, id_to_prods


# ─────────────────────────────────────────────────────
# 2. PDF MOSSO DE MAGATZEM
# ─────────────────────────────────────────────────────

def generar_pdf_mosso(layout, ruta_p2, path_out):
    """
    PDF per al mosso de magatzem — format A4 apaïsat.
    Mostra cada palet amb les dues bandes (interior/exterior)
    i els productes ordenats de baix a dalt (pesants primer).

    1 palet per pàgina per facilitar la lectura.
    """
    print(f"\nGenerant PDF mosso: {path_out}")

    doc = SimpleDocTemplate(
        path_out,
        pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()

    # Estils personalitzats
    st_titol = ParagraphStyle(
        "titol", parent=styles["Normal"],
        fontSize=16, fontName="Helvetica-Bold",
        textColor=COLOR_WHITE, alignment=TA_LEFT,
        spaceAfter=2
    )
    st_subtitol = ParagraphStyle(
        "subtitol", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica",
        textColor=COLOR_WHITE, alignment=TA_LEFT,
    )
    st_palet_header = ParagraphStyle(
        "palet_header", parent=styles["Normal"],
        fontSize=13, fontName="Helvetica-Bold",
        textColor=COLOR_WHITE, alignment=TA_CENTER,
    )
    st_banda_title = ParagraphStyle(
        "banda_title", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        textColor=COLOR_HEADER, alignment=TA_CENTER,
    )
    st_capa_nom = ParagraphStyle(
        "capa_nom", parent=styles["Normal"],
        fontSize=8, fontName="Helvetica-Bold",
        textColor=COLOR_WHITE, alignment=TA_LEFT,
        leftIndent=2
    )
    st_client = ParagraphStyle(
        "client", parent=styles["Normal"],
        fontSize=8, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#555555"), alignment=TA_LEFT,
        leftIndent=4, spaceBefore=2
    )
    st_prod = ParagraphStyle(
        "prod", parent=styles["Normal"],
        fontSize=8, fontName="Helvetica",
        textColor=COLOR_BLACK, alignment=TA_LEFT,
        leftIndent=8
    )
    st_ordre = ParagraphStyle(
        "ordre", parent=styles["Normal"],
        fontSize=7, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#888888"), alignment=TA_CENTER,
        spaceBefore=2, spaceAfter=4
    )
    st_nota = ParagraphStyle(
        "nota", parent=styles["Normal"],
        fontSize=7, fontName="Helvetica",
        textColor=colors.HexColor("#666666"), alignment=TA_LEFT,
    )

    # Informació de capçalera
    ruta_id  = layout.get("ruta", "")
    vehicle  = layout.get("vehicle", "")
    n_palets = layout.get("n_palets", 0)
    data_str = ruta_p2.get("date", "") if ruta_p2 else ""
    xofer    = ruta_p2.get("driver", "") if ruta_p2 else ""
    km       = ruta_p2.get("total_distance_km", "") if ruta_p2 else ""

    story = []

    # ── Capçalera global ──
    capçalera_data = [
        [
            Paragraph(f"📦  PLA DE CÀRREGA — Ruta {ruta_id}", st_titol),
            Paragraph(
                f"Data: {data_str}  ·  Xofer: {xofer}  ·  "
                f"Vehicle: {vehicle}  ·  {km} km  ·  {n_palets} palets",
                st_subtitol
            )
        ]
    ]
    capçalera_taula = Table(capçalera_data, colWidths=["100%"])
    capçalera_taula.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), COLOR_HEADER),
        ("PADDING",    (0,0), (-1,-1), 10),
        ("SPAN",       (0,0), (-1,-1)),
    ]))
    story.append(capçalera_taula)
    story.append(Spacer(1, 0.4*cm))

    # Llegenda de colors
    llegenda = [
        Paragraph("<b>Ordre d'apilament (de baix a dalt del palet):</b>", st_nota),
    ]
    llegenda_files = [[
        Paragraph(f"<b>■</b> {CAPA_NOMS[cat]}", st_nota)
        for cat in sorted(CAPA_COLORS.keys())
    ]]
    llegenda_t = Table(llegenda_files, colWidths=[7*cm]*4)
    llegenda_t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,0), CAPA_COLORS[1]),
        ("BACKGROUND", (1,0), (1,0), CAPA_COLORS[2]),
        ("BACKGROUND", (2,0), (2,0), CAPA_COLORS[3]),
        ("BACKGROUND", (3,0), (3,0), CAPA_COLORS[4]),
        ("TEXTCOLOR",  (0,0), (-1,-1), COLOR_WHITE),
        ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 7.5),
        ("PADDING",    (0,0), (-1,-1), 5),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("ROUNDED",    (0,0), (-1,-1), 3),
    ]))
    story.extend([
        Spacer(1, 0.2*cm),
        llegenda_t,
        Spacer(1, 0.4*cm),
    ])

    palets = layout.get("palets", [])
    pag_w = landscape(A4)[0] - 3*cm  # amplada útil

    for palet in palets:
        num    = palet["palet_num"]
        oc     = palet.get("ocupacio_pct", 0)
        zones  = ", ".join(palet.get("zones", [])[:3])
        cap    = layout.get("n_palets", 6)

        # Capçalera del palet
        oc_text = f"{oc:.0f}% ple"
        palet_header_data = [[
            Paragraph(
                f"PALET {num}  ·  {oc_text}  ·  {zones}",
                st_palet_header
            )
        ]]
        palet_header_t = Table(
            palet_header_data,
            colWidths=[pag_w]
        )
        palet_header_t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), COLOR_HEADER),
            ("PADDING",    (0,0), (-1,-1), 7),
            ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ]))

        # ── Contingut de les dues bandes ──
        def banda_contingut(fila_key, label, color_fons, ordre_txt):
            """
            Construeix el contingut d'una banda del palet.
            Ordena les capes de baix a dalt (capa 1 primer, 4 últim).
            """
            items = sorted(
                palet.get(fila_key, []),
                key=lambda c: c.get("capa_pes", 3)
            )

            cel = [Paragraph(label, st_banda_title),
                   Paragraph(ordre_txt, st_ordre)]

            if not items:
                cel.append(Paragraph("— buit —", st_nota))
                return cel

            # Agrupar per capa de pes
            per_capa = {}
            for cli in items:
                cat = cli.get("capa_pes", 3)
                per_capa.setdefault(cat, []).append(cli)

            for cat in sorted(per_capa.keys()):
                color_capa = CAPA_COLORS.get(cat, COLOR_BLUE)
                nom_capa   = CAPA_NOMS.get(cat, "")

                # Capçalera de la capa (color de fons)
                capa_rows = [[Paragraph(f"■  {nom_capa}", st_capa_nom)]]
                capa_t    = Table(capa_rows, colWidths=["100%"])
                capa_t.setStyle(TableStyle([
                    ("BACKGROUND", (0,0), (-1,-1), color_capa),
                    ("PADDING",    (0,0), (-1,-1), 4),
                    ("TOPPADDING", (0,0), (-1,-1), 3),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                ]))
                cel.append(capa_t)

                for cli in per_capa[cat]:
                    cel.append(Paragraph(f"→ {cli['client']}", st_client))
                    prods = list(zip(
                        cli.get("productes", []),
                        cli.get("descripcions", []),
                        cli.get("quantitats", []),
                    ))
                    # Ordenar per unitats: lleugers primer (a dalt del palet)
                    # barrils i pesants al final (van a baix)
                    def u_mat_mosso(mat, um="CAJ"):
                        """Unitats que ocupa el producte — per ordenar dins del mosso."""
                        m = mat.upper()
                        if any(k in m for k in ("BRL","BARRIL","TB8","CARBONICO",
                                                "ED30","TU20","VO20","DL30","DL20","ID20")):
                            return 4.0
                        return {"UN":0.5,"BOT":0.5,"EST":0.5,"PQ":0.5,
                                "PAK":0.8,"CAJ":1.0,"BRL":4.0,"TB":4.0}.get(um, 1.0)
                    # MOSSO: pesants PRIMERS (el mosso llegeix de dalt i els col·loca al FONS)
                    #        lleugers al final (van a DALT del palet)
                    prods = sorted(prods, key=lambda x: u_mat_mosso(x[0]), reverse=True)
                    for mat, desc, qty in prods:
                        desc_c = (desc[:40] + "…") if len(desc) > 40 else desc
                        cel.append(
                            Paragraph(
                                f"• {desc_c}  <b>({mat})</b>  x{qty}",
                                st_prod
                            )
                        )

            return cel

        cel_int = banda_contingut(
            "fila_interior",
            "◀  INTERIOR — fons del palet",
            COLOR_INTERIOR,
            "Carrega primer → va al fons del camió"
        )
        cel_ext = banda_contingut(
            "fila_exterior",
            "EXTERIOR — banda lona  ▶",
            COLOR_LONA,
            "Carrega segon → queda accessible des de la lona"
        )

        # Taula de dues columnes (interior | exterior) dividida en files per permetre salt de pàgina
        import itertools
        cos_data = []
        for c_int, c_ext in itertools.zip_longest(cel_int, cel_ext, fillvalue=""):
            cos_data.append([c_int, c_ext])

        cos_taula = Table(cos_data, colWidths=[pag_w/2, pag_w/2])
        cos_taula.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (0,-1), COLOR_INTERIOR),
            ("BACKGROUND",   (1,0), (1,-1), COLOR_LONA),
            ("VALIGN",       (0,0), (-1,-1), "TOP"),
            ("LINEAFTER",    (0,0), (0,-1),  1, colors.HexColor("#cccccc")),
            ("BOX",          (0,0), (-1,-1), 1.5, COLOR_HEADER),
            ("PADDING",      (0,0), (-1,-1), 2),
        ]))

        story.append(palet_header_t)
        story.append(cos_taula)
        story.append(Spacer(1, 0.3*cm))

        # Salt de pàgina entre palets
        if num < len(palets):
            story.append(PageBreak())

    doc.build(story)
    print(f"  ✅ Generat: {path_out}")


# ─────────────────────────────────────────────────────
# 3. PDF REPARTIDOR
# ─────────────────────────────────────────────────────

def generar_pdf_repartidor(layout, ruta_p2, id_to_prods, path_out):
    """
    PDF per al repartidor — format A4 vertical.
    Una parada per bloc, en ordre de visita del JSON de P2.

    Els productes apareixen en ordre de recollida del palet:
      1r → Secs i lleugers (estan a DALT del palet, s'agafen primer)
      ...
      Últim → Barrils i CO2 (estan a BAIX del palet, s'agafen últims)
    """
    print(f"\nGenerant PDF repartidor: {path_out}")

    doc = SimpleDocTemplate(
        path_out,
        pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()

    st_titol = ParagraphStyle(
        "titol_r", parent=styles["Normal"],
        fontSize=15, fontName="Helvetica-Bold",
        textColor=COLOR_WHITE, alignment=TA_LEFT,
    )
    st_sub = ParagraphStyle(
        "sub_r", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica",
        textColor=COLOR_WHITE, alignment=TA_LEFT,
        spaceBefore=2
    )
    st_stop_nom = ParagraphStyle(
        "stop_nom", parent=styles["Normal"],
        fontSize=13, fontName="Helvetica-Bold",
        textColor=COLOR_WHITE, alignment=TA_LEFT,
    )
    st_stop_zona = ParagraphStyle(
        "stop_zona", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica",
        textColor=colors.HexColor("#bbddff"), alignment=TA_LEFT,
        spaceBefore=2
    )
    st_stop_meta = ParagraphStyle(
        "stop_meta", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica",
        textColor=COLOR_WHITE, alignment=TA_LEFT,
    )
    st_badge = ParagraphStyle(
        "badge", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica-Bold",
        textColor=COLOR_WHITE, alignment=TA_CENTER,
    )
    st_prod_cap = ParagraphStyle(
        "prod_cap", parent=styles["Normal"],
        fontSize=8, fontName="Helvetica-Bold",
        textColor=COLOR_HEADER, alignment=TA_LEFT,
        spaceBefore=4
    )
    st_prod_fila = ParagraphStyle(
        "prod_fila", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica",
        textColor=COLOR_BLACK, alignment=TA_LEFT,
    )
    st_no_data = ParagraphStyle(
        "no_data", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#aaaaaa"), alignment=TA_LEFT,
    )
    st_nota_peu = ParagraphStyle(
        "nota_peu", parent=styles["Normal"],
        fontSize=7, fontName="Helvetica",
        textColor=colors.HexColor("#888888"), alignment=TA_LEFT,
    )

    pag_w = A4[0] - 3*cm

    # Construir mapa nom_client → LLISTA de palets on apareix
    # (un client pot estar repartit en més d'un palet)
    client_palet_map = {}
    for p in layout.get("palets", []):
        for cli in p.get("clients", []):
            nom_cli = cli["client"]
            client_palet_map.setdefault(nom_cli, []).append({
                "palet": p["palet_num"],
                "banda": "LONA" if cli["fila"] == "exterior" else "FONS",
                "capa_pes": cli.get("capa_pes", 3),
            })

    def _norm(s):
        """Normalitza un nom per fer matching robust."""
        import re
        s = (s or "").upper().strip()
        # treure parèntesis i contingut
        s = re.sub(r"\([^)]*\)", "", s)
        # treure caràcters no alfanumèrics (accents, comes, punts...)
        s = re.sub(r"[^A-Z0-9 ]+", " ", s)
        # col·lapsar espais
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # Índex normalitzat per fer cerca tolerant
    client_palet_map_norm = { _norm(k): v for k, v in client_palet_map.items() }

    def buscar_palets(nom_client):
        """Retorna la llista de palets/bandes on apareix el client."""
        if not nom_client:
            return []
        # 1) match exacte
        if nom_client in client_palet_map:
            return client_palet_map[nom_client]
        # 2) match normalitzat exacte
        n = _norm(nom_client)
        if n in client_palet_map_norm:
            return client_palet_map_norm[n]
        # 3) match per substring (només si una de les dues normalitzacions
        #    és prou llarga, per evitar falsos positius)
        if len(n) >= 6:
            for k_norm, v in client_palet_map_norm.items():
                if len(k_norm) >= 6 and (n in k_norm or k_norm in n):
                    return v
        return []

    def format_palet_num(palet_infos):
        """'3', '1 i 2', '1, 3 i 4' o '—' si llista buida."""
        if not palet_infos:
            return "—"
        nums = sorted({str(p["palet"]) for p in palet_infos},
                      key=lambda x: int(x) if x.isdigit() else 999)
        if len(nums) == 1:
            return nums[0]
        if len(nums) == 2:
            return f"{nums[0]} i {nums[1]}"
        return ", ".join(nums[:-1]) + f" i {nums[-1]}"

    def format_banda(palet_infos):
        """'LONA', 'FONS' o 'LONA+FONS' si està en les dues."""
        if not palet_infos:
            return "—"
        bandes = sorted({p["banda"] for p in palet_infos})
        return "+".join(bandes)

    # Mapa nom_hackaton per client_id
    id_to_nom_hack = {}
    for cid, prods in id_to_prods.items():
        # El nom no el tenim directament — el busquem al layout
        pass

    # Capçalera global
    ruta_id = layout.get("ruta", "")
    data_str = ruta_p2.get("date", "") if ruta_p2 else ""
    xofer    = ruta_p2.get("driver", "") if ruta_p2 else ""
    km       = ruta_p2.get("total_distance_km", "") if ruta_p2 else ""
    n_stops  = len(ruta_p2.get("ordered_stops", [])) if ruta_p2 else 0

    story = []

    # Capçalera
    cap_data = [[
        Paragraph(f"🚛  RUTA {ruta_id} — {xofer}", st_titol),
        Paragraph(
            f"Data: {data_str}  ·  {km} km  ·  {n_stops} parades",
            st_sub
        ),
    ]]
    cap_t = Table(cap_data, colWidths=[pag_w])
    cap_t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), COLOR_HEADER),
        ("PADDING",    (0,0), (-1,-1), 10),
        ("SPAN",       (0,0), (-1,-1)),
    ]))
    story.extend([cap_t, Spacer(1, 0.4*cm)])

    # Nota d'ordre de productes
    nota_ordre = Table([[
        Paragraph(
            "ℹ️  Els productes de cada parada apareixen en l'ordre de recollida del palet: "
            "primer els que estan a <b>DALT</b> (secs i lleugers), "
            "últims els que estan a <b>BAIX</b> (barrils i CO2).",
            st_nota_peu
        )
    ]], colWidths=[pag_w])
    nota_ordre.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#eaf4fb")),
        ("PADDING",    (0,0), (-1,-1), 7),
        ("BOX",        (0,0), (-1,-1), 0.5, COLOR_BLUE),
    ]))
    story.extend([nota_ordre, Spacer(1, 0.4*cm)])

    # Parades
    stops = ruta_p2.get("ordered_stops", []) if ruta_p2 else []

    for stop in stops:
        num       = stop.get("stop_number", "")
        nom       = stop.get("client_name", "")
        cid_check = str(stop.get("client_id", ""))

        # Saltar NOMÉS si no té productes al Hackaton
        # (clients nous sense historial de comandes)
        te_prods = cid_check in id_to_prods
        if not te_prods:
            continue

        cid   = cid_check
        eta   = stop.get("eta", "")
        zona  = stop.get("zone", "")
        poble = stop.get("town", "")
        tw_i  = stop.get("time_window_start", "")[:5]
        tw_f  = stop.get("time_window_end", "")[:5]
        tw_ok = stop.get("time_window_status", "") == "on_time"
        dist  = stop.get("distance_from_previous_km", 0)
        units = stop.get("delivery_units", 0)
        ret   = stop.get("expected_return_units", 0)

        # Buscar info del/s palet/s on és aquest client
        prods_hack = id_to_prods.get(cid, [])

        palet_infos = buscar_palets(nom)
        palet_num   = format_palet_num(palet_infos)
        banda       = format_banda(palet_infos)
        # Etiqueta per al text "Palet 3" / "Palets 1 i 2"
        palet_label = "Palets" if palet_infos and len({p["palet"] for p in palet_infos}) > 1 else "Palet"

        if not palet_infos:
            print(f"  ⚠️  Sense palet assignat: {nom} (id={cid})")

        # ── Capçalera de la parada ──
        color_header_stop = COLOR_HEADER if tw_ok else COLOR_ORANGE
        stop_header = [
            [
                Paragraph(f"{num}", st_badge),
                Paragraph(f"{nom}", st_stop_nom),
                Paragraph(f"🕐 {eta}", st_stop_meta),
            ]
        ]
        stop_header_t = Table(
            stop_header,
            colWidths=[1*cm, pag_w - 4*cm, 3*cm]
        )
        stop_header_t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), color_header_stop),
            ("PADDING",    (0,0), (-1,-1), 8),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN",      (0,0), (0,0),   "CENTER"),
            ("BACKGROUND", (0,0), (0,0),   COLOR_BLUE),
        ]))

        # ── Meta-info ──
        badge_tw = "✅ On time" if tw_ok else "⚠️ Fora finestra"
        meta_text = (
            f"📍 {zona} — {poble}  ·  "
            f"Finestra: {tw_i}–{tw_f}  ·  {badge_tw}  ·  "
            f"📦 {units} unitats  ·  ↩ Retorns: {ret}  ·  "
            f"🛣 +{dist:.1f} km"
        )
        meta_data = [[Paragraph(meta_text, st_nota_peu)]]
        meta_t    = Table(meta_data, colWidths=[pag_w])
        meta_t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#eaecee")),
            ("PADDING",    (0,0), (-1,-1), 6),
        ]))

        # ── Badge palet ──
        # color taronja-clar si el client està repartit en més d'un palet
        # (avís visual: el repartidor ha d'anar a buscar a dos palets)
        is_multi = palet_infos and len({p["palet"] for p in palet_infos}) > 1
        if banda == "—":
            color_banda = colors.HexColor("#bdc3c7")
        elif "+" in banda:
            color_banda = COLOR_ORANGE
        elif banda == "LONA":
            color_banda = COLOR_BLUE
        else:
            color_banda = colors.HexColor("#7f8c8d")

        color_palet = COLOR_ORANGE if is_multi else COLOR_HEADER

        badge_data  = [[
            Paragraph(f"{palet_label} {palet_num}", st_badge),
            Paragraph(banda, st_badge),
        ]]
        # més amplada a la columna del palet quan són múltiples
        col_palet_w = 4*cm if is_multi else 2.8*cm
        badge_t = Table(badge_data, colWidths=[col_palet_w, 2.5*cm])
        badge_t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (0,-1), color_palet),
            ("BACKGROUND", (1,0), (1,-1), color_banda),
            ("PADDING",    (0,0), (-1,-1), 5),
            ("ALIGN",      (0,0), (-1,-1), "CENTER"),
            ("FONTNAME",   (0,0), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 9),
            ("TEXTCOLOR",  (0,0), (-1,-1), COLOR_WHITE),
        ]))

        # ── Taula de productes ──
        # ORDRE INVERS: capa 4 (DALT del palet) → capa 1 (BASE)
        # Perquè el repartidor agafa primer el que està a dalt
        prod_rows = [[
            Paragraph("<b>Producte</b>", st_prod_cap),
            Paragraph("<b>Codi</b>", st_prod_cap),
            Paragraph("<b>Qtd</b>", st_prod_cap),
            Paragraph("<b>UM</b>", st_prod_cap),
        ]]
        prod_colors_files = [colors.HexColor("#e8f4fd")]  # capçalera

        if prods_hack:
            # Ordenar per unitats que ocupa cada producte (ascending):
            # lleugers primer (estan a DALT del palet, s'agafen primer)
            # barrils al final (estan a BAIX del palet, s'agafen últims)
            # Unitats que ocupa cada producte al palet:
            # CAJ = 1 (caixa d'ampolles), BRL/TB = 4 (barril o tub CO2)
            # llaunes (CAJ petita) = 0.8, UN/BOT/EST/PAK = 0.5 (unitat individual)
            # Els barrils (BRL al codi o al UM) sempre 4 unitats
            UNITATS_UM = {
                "UN":  0.5,   # Unitat individual (llauna, bossa, pot...)
                "BOT": 0.5,   # Botella individual
                "EST": 0.5,   # Estoig/display individual
                "PQ":  0.5,   # Paquet individual
                "PAK": 0.8,   # Pack (similar a caixa de llaunes)
                "CAJ": 1.0,   # Caixa estàndard d'ampolles
                "BRL": 4.0,   # Barril de cervesa
                "TB":  4.0,   # Tub de CO2
            }
            def unitats_prod(mat, um):
                """Calcula les unitats de palet que ocupa un producte."""
                m = mat.upper()
                # Barrils (per codi de material o UM)
                if any(k in m for k in ("BRL","BARRIL","TB8","CARBONICO",
                                        "ED30","TU20","VO20","DL30","DL20","ID20")):
                    return 4.0
                return UNITATS_UM.get(um, 1.0)

            # REPARTIDOR: lleugers PRIMERS (estan a dalt del palet, s'agafen primer)
            #             barrils AL FINAL (estan a baix, s'agafen últims)
            prods_ordenats = sorted(
                prods_hack,
                key=lambda x: unitats_prod(x[0], x[3])
            )

            for mat, desc, qty, um in prods_ordenats:
                desc_c = (desc[:38] + "…") if len(desc) > 38 else desc
                prod_rows.append([
                    Paragraph(desc_c, st_prod_fila),
                    Paragraph(mat, st_prod_fila),
                    Paragraph(str(qty), st_prod_fila),
                    Paragraph(um, st_prod_fila),
                ])
                prod_colors_files.append(colors.white)

        prod_t = Table(
            prod_rows,
            colWidths=[pag_w - 4.5*cm, 2*cm, 1.3*cm, 1.2*cm]
        )
        prod_t_style = [
            ("FONTNAME",    (0,0), (-1,0),   "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,-1),  8.5),
            ("PADDING",     (0,0), (-1,-1),  4),
            ("VALIGN",      (0,0), (-1,-1),  "MIDDLE"),
            ("LINEBELOW",   (0,0), (-1,0),   0.5, colors.HexColor("#cccccc")),
            ("LINEBELOW",   (0,-1),(- 1,-1), 0.5, colors.HexColor("#cccccc")),
            ("BACKGROUND",  (0,0), (-1,0),   colors.HexColor("#e8f4fd")),
            ("BOX",         (0,0), (-1,-1),  0.5, colors.HexColor("#cccccc")),
        ]
        # Colors de les files de capa
        for i, col in enumerate(prod_colors_files):
            if i > 0:
                prod_t_style.append(
                    ("BACKGROUND", (0,i), (-1,i), col)
                )
                if col != colors.white:
                    prod_t_style.append(
                        ("TEXTCOLOR", (0,i), (-1,i), COLOR_WHITE)
                    )
                    prod_t_style.append(
                        ("FONTNAME", (0,i), (-1,i), "Helvetica-Bold")
                    )
                    prod_t_style.append(
                        ("SPAN", (0,i), (-1,i))
                    )

        prod_t.setStyle(TableStyle(prod_t_style))

        # Muntar el bloc de la parada
        bloc_elements = [
            stop_header_t,
            meta_t,
            Spacer(1, 0.2*cm),
            badge_t,
            Spacer(1, 0.15*cm),
            prod_t,
            Spacer(1, 0.4*cm),
        ]
        story.append(KeepTogether(bloc_elements))

    doc.build(story)
    print(f"  ✅ Generat: {path_out}")


# ─────────────────────────────────────────────────────
# 4. FUNCIÓ PRINCIPAL
# ─────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  DAMM ROUTES — Generació de PDFs")
    print("=" * 50)

    layout, ruta_p2, id_to_prods = carregar_dades()

    generar_pdf_mosso(layout, ruta_p2, PATH_OUT_MOSSO)
    generar_pdf_repartidor(layout, ruta_p2, id_to_prods, PATH_OUT_REP)

    print("\n✅ Tot llest!")
    print(f"   → {PATH_OUT_MOSSO}   (PDF mosso de magatzem)")
    print(f"   → {PATH_OUT_REP}   (PDF repartidor)")


if __name__ == "__main__":
    main()
