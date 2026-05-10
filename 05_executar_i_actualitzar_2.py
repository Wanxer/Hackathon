"""
03_assignar_carrega.py
======================
P3 — Optimització de càrrega del camió Damm

LÒGICA PRINCIPAL:
  El transportista NO baixa el palet del camió.
  Accedeix als productes directament amb la mà
  des del lateral (lona corredissa).

  Dins de cada palet hi ha dues files:
    ┌──────────────────────────────────────┐
    │  FILA INTERIOR  │  FILA EXTERIOR     │ ← Lona (accés)
    │  (fons palet)   │  (banda lona)      │
    └──────────────────────────────────────┘

  El transportista arriba a la parada, obre la lona
  i agafa els productes de la FILA EXTERIOR.
  Els de la FILA INTERIOR no els pot agafar fins que
  la fila exterior d'aquell palet estigui buida.

  Per tant, l'ordre de col·locació dins el palet és:
    - Primera parada de la zona → FILA EXTERIOR
    - Parades posteriors        → FILA INTERIOR

Inputs:
  - data/Hackaton.xlsx           (comandes per client)
  - data/ZM040.xlsx              (dimensions i pesos)
  - data/ruta_optimitzada.json   (ordre zones, ve de P2)
    → Si no existeix, usa ordre per volum descendent

Outputs:
  - layout_camio.json   (per a P4/Streamlit)

Execució:
  pip install pandas openpyxl plotly
  python 03_assignar_carrega.py
"""

import pandas as pd
import json
import os
import warnings
warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────
# CONFIGURACIÓ GLOBAL
# Canvia aquí per provar amb altres rutes o vehicles
# ─────────────────────────────────────────────────────

RUTA_OBJECTIU  = "DR0027"       # Ruta a optimitzar
DATA_OBJECTIU  = "24/02/2026"   # Data concreta (None = totes)
# Llista de client_ids a processar (None = agafar tots els de la ruta+data).
# Quan el 05 sobreescriu aquesta variable amb els clients del JSON,
# preparar_ruta filtrarà pels clients indicats i agafarà l'última
# comanda de cada un, ignorant DATA_OBJECTIU.
CLIENT_IDS_OBJECTIU = None

# Definició dels vehicles disponibles:
#   palets      → nombre total de palets que caben al camió
#   vol_palet   → volum útil per palet en metres cúbics (m3)
#   pes_max_kg  → pes màxim total que pot portar el vehicle (kg)
#   acces       → "lateral"   = lona corredissa (accés per tot el lateral)
#                 "posterior" = porta del darrere (cal ordre LIFO)
VEHICLES = {
    "camio_gran":  {"palets": 8, "vol_palet_m3": 0.90, "pes_max_kg": 5000, "acces": "lateral"},
    "camio_mitja": {"palets": 6, "vol_palet_m3": 0.72, "pes_max_kg": 3000, "acces": "lateral"},
    "furgoneta":   {"palets": 3, "vol_palet_m3": 0.40, "pes_max_kg": 800,  "acces": "posterior"},
}

# Codis de materials que són envasos retornables
# Van als espais buits dels palets existents — NO fan falta palet propi
# El transportista els recull a cada parada i els col·loca on hi hagi espai
PREFIXOS_RETORN = ("3ENV", "CJ")

# Categories de pes per determinar l'ordre d'apilament dins el palet
# REGLA: mai posar productes pesants sobre productes lleugers/fràgils
# Ordre de càrrega de baix a dalt: PESANT → MIG → LLEUGER → FRÀGIL
CATEGORIA_PES = {
    # Nivell 1 — BASE: van sempre a baix (mai sobre llaunes o lleugers)
    # Barrils de cervesa, tubs de CO2 — molt pesants i rígids
    "molt_pesant": {
        "keywords": ("BRL", "BARRIL", "TB8", "CARBONICO"),
        "ums":      ("BRL", "TB"),
        "nivell":   1,
        "label":    "BASE — barrils i CO2",
    },
    # Nivell 2 — MIG-BAIX: caixes de cervesa retornable (vidre, pesades)
    "pesant": {
        "keywords": ("ED13", "VO13", "FD13", "EC13", "TU13", "DL13",
                     "ED15", "VO15", "1/3 RET", "1/5 RET"),
        "ums":      ("CAJ",),
        "nivell":   2,
        "label":    "MIG-BAIX — cerveses vidre",
    },
    # Nivell 3 — MIG: aigues, refrescos, vins (pes mig)
    "mig": {
        "keywords": ("AGUA", "VERI", "VICHY", "FONT D", "COCA COLA",
                     "AQUARIUS", "NESTEA", "GASEOSA", "VINO", "VINO"),
        "ums":      ("CAJ",),
        "nivell":   3,
        "label":    "MIG — aigues i refrescos",
    },
    # Nivell 4 — DALT: lleugers, fràgils, secs (mai res pesant a sobre)
    "lleuger": {
        "keywords": ("LATA", "LLAUNA", "PACK", "PAPER", "SERVILLETA",
                     "CAFE", "SUCRE", "EDULCOR", "LOTUS", "BONKA"),
        "ums":      ("UN", "PAK", "BOT", "EST", "ZPR", "PQ"),
        "nivell":   4,
        "label":    "DALT — lleugers i fràgils",
    },
}

def categoria_producte(material, denominacio, um):
    """
    Determina la categoria de pes d'un producte per saber
    on s'ha de col·locar al palet (de baix a dalt).
    Retorna un enter: 1=base, 2=mig-baix, 3=mig, 4=dalt
    """
    mat_up = str(material).upper()
    den_up = str(denominacio).upper()
    um_up  = str(um).upper()

    for cat_nom, cat in CATEGORIA_PES.items():
        # Comprovar per unitat de mesura
        if um_up in cat["ums"]:
            return cat["nivell"], cat["label"]
        # Comprovar per paraula clau al codi o descripció
        for kw in cat["keywords"]:
            if kw.upper() in mat_up or kw.upper() in den_up:
                return cat["nivell"], cat["label"]

    # Per defecte: categoria mig
    return 3, "MIG — altres"

# Unitats de palet que ocupa cada producte segons la seva UM
# Aquestes són les unitats reals que hem de comptar per saber
# quant espai ocupa cada producte al palet
# 1.0 = una caixa d'ampolles ocupa 1 unitat de palet
# 4.0 = un barril ocupa 4 unitats de palet (gran i pesat)
# 0.8 = una caixa de llaunes ocupa 0.8 unitats (més plana)
# 4.0 = un tub de CO2 ocupa 4 unitats (alt i pesat)
UNITATS_PALET_PER_UM = {
    "CAJ": 1.0,    # Caixa d'ampolles/producte → 1 unitat
    "BRL": 4.0,    # Barril → 4 unitats (gran i pesat)
    "UN":  0.1,    # Unitat individual (ampolla, got...) → molt petit
    "BOT": 0.1,    # Botella individual → molt petit
    "PAK": 0.5,    # Pack → mig
    "ZPR": 0.1,    # Producte propi petit → molt petit
    "TB":  4.0,    # Tub de CO2 → 4 unitats (com un barril)
    "EST": 0.2,    # Estoig → petit
    "PQ":  0.2,    # Paquet → petit
}

# Capacitat total del palet en "unitats de palet"
# 1 CAJ (caixa ampolles) = 1 unitat
# 1 BRL (barril) = 4 unitats
# 1 TB  (tub CO2) = 4 unitats
# 1 CAJ llaunes   = 0.8 unitats
# Un palet aguanta 60 unitats (= 60 caixes d'ampolles)
CAPACITAT_PALET = {
    "camio_gran":  60,   # 60 unitats per palet
    "camio_mitja": 60,   # 60 unitats per palet
    "furgoneta":   30,   # 30 unitats per palet (vehicle petit)
}

# Pes estimat per defecte (kg) per unitat de cada UM
PES_PER_UM = {
    "CAJ": 12.0,
    "BRL": 25.0,
    "UN":  0.5,
    "BOT": 1.0,
    "PAK": 2.0,
    "ZPR": 0.5,
    "TB":  8.0,
    "EST": 1.0,
    "PQ":  1.0,
}


# ─────────────────────────────────────────────────────
# 1. CÀRREGA DE DADES
# ─────────────────────────────────────────────────────

def carregar_dades(path_hackaton="data/Hackaton.xlsx",
                   path_zm040="data/ZM040.xlsx"):
    """
    Llegeix els dos fitxers Excel principals de Damm:
      - Hackaton.xlsx → totes les comandes (82.849 línies)
      - ZM040.xlsx    → dimensions i pesos de cada producte

    Retorna dos DataFrames: df (comandes) i zm (dimensions)
    """
    print("Carregant dades...")
    df = pd.read_excel(path_hackaton, sheet_name="Detalle entrega")
    zm = pd.read_excel(path_zm040)
    print(f"  Hackaton.xlsx : {len(df):,} linies")
    print(f"  ZM040.xlsx    : {len(zm):,} productes")
    return df, zm


# ─────────────────────────────────────────────────────
# 2. TAULA DE DIMENSIONS (ZM040)
# ─────────────────────────────────────────────────────

def preparar_dimensions(zm):
    """
    Extreu el pes (kg) per producte del ZM040.
    Ja no usem m3 — usem UNITATS_PALET_PER_UM per calcular l'espai.

    El ZM040 segueix sent útil per al pes real de cada producte,
    que necessitem per la lògica d'apilament (no posar pesants sobre lleugers).

    Retorna un DataFrame indexat per Material amb columna pes_kg
    """
    zm_pes = zm[zm["Peso bruto"] > 0].drop_duplicates("Material")
    dims = zm_pes.set_index("Material")[["Peso bruto"]].copy()
    dims.columns = ["pes_kg"]

    print(f"  Pesos trobats al ZM040: {len(dims)} productes")
    return dims


# ─────────────────────────────────────────────────────
# 3. FILTRAR RUTA I CALCULAR VOLUMS
# ─────────────────────────────────────────────────────

def preparar_ruta(df, dims, ruta_id, data=None, client_ids=None):
    """
    Filtra les comandes d'una ruta i data concreta i
    calcula el volum i pes de cada línia de comanda.

    Si `client_ids` està definit, filtra també pels clients indicats
    i agafa NOMÉS l'última comanda disponible per a cada client
    (independentment de la data hardcodada). Aquest és el cas
    quan el 05 ens passa els clients del ruta_optimitzada.json.

    Passos:
      1. Filtrar per ruta (i data si s'especifica)
      2. Separar retornables de comandes reals
         Els retornables (CJ13, BRL30V...) no s'han de carregar
         al camió, sinó recollir als clients → van a palet separat
      3. Calcular vol_m3 i pes_kg per cada línia:
         - Si el material té dimensions al ZM040 → usa les reals
         - Si no → usa el valor per defecte segons la UM

    Retorna: (comandes, retornables) — dos DataFrames
    """
    print(f"\nPreparant ruta {ruta_id}" + (f" - {data}" if data else ""))

    if client_ids:
        # Mode "guiat pel JSON": ens igual sota quina ruta apareguin
        # els clients al Hackaton — el JSON ja és la veritat de qui toca avui.
        # (Aquesta és la mateixa lògica que fa servir el 04.)
        cids_norm = {str(c).split(".")[0] for c in client_ids}
        col_cli   = "Destinatario mcía..1"
        dr        = df.copy()
        dr["_cid_norm"] = dr[col_cli].astype(str).str.split(".").str[0]
        dr = dr[dr["_cid_norm"].isin(cids_norm)].copy()

        # Per cada client, agafar només l'última comanda (FECHA màxima)
        if "FECHA" in dr.columns and not dr.empty:
            ultima_data = dr.groupby("_cid_norm")["FECHA"].transform("max")
            dr = dr[dr["FECHA"] == ultima_data].copy()

        dr = dr.drop(columns=["_cid_norm"])

        n_buscats  = len(cids_norm)
        n_trobats  = dr[col_cli].nunique()
        n_no_trobats = n_buscats - n_trobats
        rutes_uniques = sorted(dr["Ruta"].dropna().unique().tolist())
        print(f"  Mode JSON: {n_trobats}/{n_buscats} clients trobats al Hackaton")
        if rutes_uniques:
            print(f"  Rutes del Hackaton on apareixen: {rutes_uniques}")
        if n_no_trobats:
            no_trobats_ids = sorted(cids_norm - set(dr["_cid_norm"].unique()) if False else cids_norm - set(dr[col_cli].astype(str).str.split(".").str[0].unique()))
            print(f"  ⚠️  {n_no_trobats} clients del JSON NO apareixen al Hackaton:")
            for cid in no_trobats_ids[:10]:
                print(f"      → {cid}")
            if len(no_trobats_ids) > 10:
                print(f"      ... i {len(no_trobats_ids)-10} més")
    else:
        # Mode antic: filtrar per ruta + data (comportament original)
        dr = df[df["Ruta"] == ruta_id].copy()
        if data:
            dr = dr[dr["FECHA"] == data].copy()

    # Separar retornables (envasos buits que es recullen als clients)
    # El prefix del codi de material identifica si és retornable
    mask_ret    = dr["Material"].str.startswith(PREFIXOS_RETORN, na=False)
    retornables = dr[mask_ret].copy()
    comandes    = dr[~mask_ret].copy()

    print(f"  Comandes: {len(comandes)} linies | "
          f"Clients: {comandes['Nombre 1'].nunique()} | "
          f"Zones: {comandes['ZonaTransp.1'].nunique()}")

    def calc_unitats_palet(row):
        """
        Calcula les unitats de palet que ocupa una línia de comanda.

        Fórmula: unitats_palet_per_um × quantitat_entregada

        Exemples reals:
          3 CAJ de cervesa  → 3 × 1.0 = 3.0 unitats
          2 BRL de cervesa  → 2 × 4.0 = 8.0 unitats
          5 CAJ de llaunes  → 5 × 0.8 = 4.0 unitats  (més planes)
          1 TB  de CO2      → 1 × 4.0 = 4.0 unitats  (alt i pesat)
        """
        um = row["Un.medida venta"]   # CAJ, BRL, UN, BOT, TB...
        q  = row["Cantidad entrega"]  # quantitat entregada
        return UNITATS_PALET_PER_UM.get(um, 0.5) * q

    def calc_pes(row):
        """
        Calcula el pes total d'una línia de comanda (kg).
        S'usa per la lògica d'apilament: no posar pesants sobre lleugers.
        Usem PES_PER_UM perquè el ZM040 pot tenir el pes del palet sencer.
        """
        um = row["Un.medida venta"]
        q  = row["Cantidad entrega"]
        return PES_PER_UM.get(um, 1.0) * q

    # Afegir les columnes calculades al DataFrame de comandes
    # "unitats_palet" substitueix "vol_m3" com a mesura d'espai
    comandes["unitats_palet"] = comandes.apply(calc_unitats_palet, axis=1)
    comandes["pes_kg"]        = comandes.apply(calc_pes, axis=1)

    return comandes, retornables


# ─────────────────────────────────────────────────────
# 4. AGREGAR PER CLIENT I ZONA
# ─────────────────────────────────────────────────────

def agregar_per_client(comandes):
    """
    Agrupa les línies de comanda per client i per zona geogràfica.

    Genera dues taules:
      - per_client → 1 fila per client amb volum, pes i llista de productes
      - per_zona   → 1 fila per zona amb totals agregats

    Les zones (ZonaTransp) són les àrees geogràfiques on el camió
    para i el transportista reparteix a peu als clients propers.
    Una zona pot tenir entre 1 i 44 clients.
    """
    print("\nAgregant per client i zona...")

    # Agrupar per client: suma volums i pesos, llista productes
    per_client = comandes.groupby(
        ["ZonaTransp", "ZonaTransp.1", "Nombre 1", "Calle", "Población"]
    ).agg(
        n_linies        = ("Material", "count"),
        total_caixes    = ("Cantidad entrega", "sum"),
        unitats_palet   = ("unitats_palet", "sum"),  # espai al palet
        pes_kg          = ("pes_kg", "sum"),
        productes       = ("Material", list),
        quantitats      = ("Cantidad entrega", list),
        descripcions    = ("Denominación", list),
        ums             = ("Un.medida venta", list),  # per calcular categoria pes
    ).reset_index()

    per_client.columns = [
        "zona_id", "zona_nom", "client", "carrer", "poblacio",
        "n_linies", "total_caixes", "unitats_palet", "pes_kg",
        "productes", "quantitats", "descripcions", "ums",
    ]

    # Agrupar per zona: suma de tots els clients de la zona
    per_zona = per_client.groupby(["zona_id", "zona_nom"]).agg(
        n_clients           = ("client", "count"),
        unitats_palet_total = ("unitats_palet", "sum"),
        pes_total_kg        = ("pes_kg", "sum"),
        total_caixes        = ("total_caixes", "sum"),
    ).reset_index()

    print(f"  Zones: {len(per_zona)} | Clients: {len(per_client)}")
    print(f"  Unitats palet total: {per_zona['unitats_palet_total'].sum():.1f} | "
          f"Pes: {per_zona['pes_total_kg'].sum():.0f} kg")

    return per_client, per_zona


# ─────────────────────────────────────────────────────
# 5. ORDRE DE RUTA (de P2 o baseline)
# ─────────────────────────────────────────────────────

def carregar_ordre_ruta(per_zona,
                        path_json="data/ruta_optimitzada.json"):
    """
    Determina l'ordre de visita de les zones.

    Cas 1 — P2 ha entregat el JSON optimitzat:
      Llegeix l'ordre calculat per OR-Tools i reordena les zones.
      El JSON pot tenir:
        - "zone_sequence": llista de noms de zona en ordre de visita
        - "ordre_zones":   llista de zone_ids (format antic)

    Cas 2 — El JSON no existeix (fase inicial o mode test):
      Usa un ordre baseline: zones de major a menor volum.
      Això no és òptim però permet treballar sense esperar P2.
    """
    if os.path.exists(path_json):
        print(f"\nOrdre de P2: {path_json}")
        with open(path_json, encoding="utf-8") as f:
            ruta_p2 = json.load(f)

        # Intentar llegir zone_sequence (format nou) o ordre_zones (antic)
        ordre = ruta_p2.get("zone_sequence", ruta_p2.get("ordre_zones", []))

        if ordre:
            # Crear índex: zona → posició a la ruta
            idx_nom = {z: i for i, z in enumerate(ordre)}

            # Intentar fer match per zona_nom (noms del JSON)
            per_zona["ordre"] = per_zona["zona_nom"].map(idx_nom)

            # Si no fa match per nom, provar per zona_id
            mask_miss = per_zona["ordre"].isna()
            if mask_miss.any():
                per_zona.loc[mask_miss, "ordre"] = (
                    per_zona.loc[mask_miss, "zona_id"].map(idx_nom)
                )

            # Zones que no surten al JSON van al final
            per_zona["ordre"] = per_zona["ordre"].fillna(999)
            per_zona = per_zona.sort_values("ordre").reset_index(drop=True)

            n_ok = (per_zona["ordre"] < 999).sum()
            print(f"  Zones ordenades per P2: {n_ok}/{len(per_zona)}")
            for _, z in per_zona.iterrows():
                pos = int(z['ordre']) + 1 if z['ordre'] < 999 else '?'
                print(f"    {pos}. {z['zona_nom']} "
                      f"({z['n_clients']} clients, "
                      f"{z['unitats_palet_total']:.1f}u)")
        else:
            print("  JSON trobat pero sense zone_sequence ni ordre_zones")
            per_zona = per_zona.sort_values(
                "unitats_palet_total", ascending=False
            ).reset_index(drop=True)
    else:
        print("\nruta_optimitzada.json no trobat "
              "-> ordre per volum descendent (baseline)")
        per_zona = per_zona.sort_values(
            "unitats_palet_total", ascending=False
        ).reset_index(drop=True)

    return per_zona


# ─────────────────────────────────────────────────────
# 5b. SELECCIÓ AUTOMÀTICA DE VEHICLE
# ─────────────────────────────────────────────────────

def triar_vehicle(per_zona):
    """
    Tria automàticament el vehicle més petit que pot
    transportar tota la càrrega de la ruta.

    Ordre de preferència (de petit a gran):
      1. furgoneta   →  3 palets × 30u =  90u,  800 kg
      2. camio_mitja →  6 palets × 60u = 360u, 3000 kg
      3. camio_gran  →  8 palets × 60u = 480u, 5000 kg

    Criteris:
      - Palets necessaris (ceil(unitats / cap_palet)) <= palets disponibles
      - Pes total <= pes_max_kg del vehicle
    Si no cap en cap vehicle, tria el més gran.
    """
    import math
    total_uni = per_zona["unitats_palet_total"].sum()
    total_pes = per_zona["pes_total_kg"].sum()

    # Ordre: del més petit al més gran
    ordre_vehicles = ["furgoneta", "camio_mitja", "camio_gran"]

    print(f"\nSeleccio automatica de vehicle:")
    print(f"  Carrega total: {total_uni:.1f} unitats, {total_pes:.0f} kg")

    for nom in ordre_vehicles:
        v = VEHICLES[nom]
        cap = CAPACITAT_PALET[nom]
        palets_necessaris = math.ceil(total_uni / cap) if cap > 0 else 999
        palets_disponibles = v["palets"]
        pes_ok    = total_pes <= v["pes_max_kg"]
        vol_ok    = palets_necessaris <= palets_disponibles

        marca = "[OK]" if (pes_ok and vol_ok) else "[NO]"
        motiu = []
        if not vol_ok:
            motiu.append(f"necessita {palets_necessaris} palets, nomes te {palets_disponibles}")
        if not pes_ok:
            motiu.append(f"{total_pes:.0f}kg > {v['pes_max_kg']}kg max")

        print(f"  {marca} {nom:14s} -> {palets_disponibles} palets x {cap}u "
              f"| {v['pes_max_kg']}kg max"
              + (f"  ({'; '.join(motiu)})" if motiu else ""))

        if vol_ok and pes_ok:
            print(f"  -> Vehicle triat: {nom} "
                  f"({palets_necessaris}/{palets_disponibles} palets usats)")
            return nom

    # Si no cap en cap, triar el mes gran
    print(f"  AVIS: La carrega no cap en cap vehicle! Usant {ordre_vehicles[-1]}")
    return ordre_vehicles[-1]


# ─────────────────────────────────────────────────────
# 6. ASSIGNAR CÀRREGA ALS PALETS  ← peça principal
# ─────────────────────────────────────────────────────

def assignar_carrega(per_client, per_zona, vehicle_tipus):
    """
    Assigna cada client a un palet del camió respectant
    la lògica d'accessibilitat per files.

    REGLA CLAU D'ACCESSIBILITAT:
      El transportista accedeix als productes directament
      amb la mà des del lateral del camió (lona corredissa).
      NO baixa el palet sencer.

      Dins de cada palet hi ha dues files:
        ┌─────────────────────────────────┐
        │  FILA INTERIOR  │  FILA EXTERIOR│ ← Lona (accés)
        │  (fons palet)   │  (banda lona) │
        └─────────────────────────────────┘

      El transportista arriba a la parada, obre la lona
      i agafa els productes de la FILA EXTERIOR.
      Els de la FILA INTERIOR no els pot agafar fins que
      la fila exterior d'aquell palet estigui buida.

    CONSEQÜÈNCIA PER A L'ORDRE DE COL·LOCACIÓ:
      Dins d'un palet amb clients de la mateixa zona:
        - El primer client a visitar → FILA EXTERIOR
        - Els clients posteriors     → FILA INTERIOR
      D'aquesta manera el transportista agafa els productes
      en ordre sense haver de moure res.

    REGLES D'ASSIGNACIÓ DE PALETS:
      1. Reservar l'últim palet per retornables (sempre accessible)
      2. Intentar que tots els clients d'una zona vagin al mateix palet
      3. Si la zona no hi cap sencera, distribuir clients
         al palet amb més espai lliure
      4. Furgoneta (accés posterior) → ordre LIFO invers

    Retorna: llista de palets amb clients assignats i metadades
    """
    print(f"\nAssignant carrega -> {vehicle_tipus}")

    v            = VEHICLES[vehicle_tipus]
    n_palets     = v["palets"]
    cap_palet    = CAPACITAT_PALET[vehicle_tipus]  # unitats de palet per palet
    pes_max      = v["pes_max_kg"]
    acces        = v["acces"]
    # Tots els palets disponibles per a productes
    # Els retornables van als espais buits — no cal reservar-ne cap
    palets_prod  = n_palets

    print(f"  Palets disponibles : {palets_prod}")
    print(f"  Capacitat palet    : {cap_palet} unitats per palet")
    print(f"  (CAJ=1.0, BRL=4.0, llaunes=0.8, TB=4.0)")

    # Inicialitzar estructura de palets buits
    # Cada palet té dues llistes: fila_exterior i fila_interior
    palets = [{
        "palet_num":       i + 1,
        "clients":         [],    # Tots els clients (en ordre de visita)
        "zones":           [],    # Zones representades en aquest palet
        "fila_exterior":   [],    # Clients a la banda de la lona (accessibles)
        "fila_interior":   [],    # Clients al fons (accessibles quan exterior buit)
        "unitats_palet":   0.0,   # Unitats de palet acumulades (CAJ=1, BRL=4...)
        "pes_acumulat":    0.0,
        "total_caixes":    0,
        "n_clients":       0,
    } for i in range(palets_prod)]

    # Furgoneta: no dividir en bandes (accés només posterior)
    # Camió: exterior/interior és etiqueta d'ordre d'accés, no constraint de capacitat
    es_furgoneta = (acces == "posterior")

    def espai_lliure(p):
        """Unitats de palet disponibles en total."""
        return max(0.0, cap_palet - p["unitats_palet"])

    # Capacitat per banda (cada palet té dues bandes: exterior/interior)
    # Si forcem 50/50 estricte, fragmentem clients innecessàriament.
    # Permetem desbalanç moderat: una banda pot tenir fins al 65% del palet,
    # cosa que evita que un palet es carregui tot a una banda i caigui.
    cap_per_banda_max = cap_palet * 0.65 if not es_furgoneta else cap_palet

    def usat_fila(p, fila):
        """Unitats acumulades en una banda concreta."""
        return sum(c["unitats_palet"] for c in p[f"fila_{fila}"])

    def espai_lliure_fila(p, fila):
        """
        Espai disponible en una banda concreta del palet.
        Limitat per cap_per_banda_max per evitar palets desequilibrats.
        En furgoneta, com que no hi ha laterals, no apliquem el límit.
        """
        if es_furgoneta:
            return espai_lliure(p)
        # No es pot ocupar més que (a) el que queda al palet
        # ni (b) el que queda fins al límit per banda
        per_palet = espai_lliure(p)
        per_fila  = max(0.0, cap_per_banda_max - usat_fila(p, fila))
        return min(per_palet, per_fila)

    def cat_dominant(cr):
        """Categoria de pes dominant del client (1=pesant...4=lleuger)."""
        ums = cr.get("ums") or ["CAJ"] * len(cr["productes"])
        cats = [
            categoria_producte(m, d, u)[0]
            for m, d, u in zip(cr["productes"], cr["descripcions"], ums)
        ]
        return min(cats) if cats else 3

    def afegir_entrada(p, nom, zona_id, zona_nom, carrer, poblacio,
                       uni, pes, caixes, n_linies,
                       productes, quantitats, descripcions, cat, fila):
        """Afegeix un fragment (o client sencer) al palet p."""
        e = {
            "client":        nom,
            "zona_id":       zona_id,
            "zona_nom":      zona_nom,
            "carrer":        carrer,
            "poblacio":      poblacio,
            "unitats_palet": round(uni, 2),
            "pes_kg":        round(pes, 1),
            "total_caixes":  caixes,
            "n_linies":      n_linies,
            "productes":     productes,
            "quantitats":    quantitats,
            "descripcions":  descripcions,
            "capa_pes":      cat,
            "fila":          fila,
        }
        p["clients"].append(e)
        if fila == "exterior":
            p["fila_exterior"].append(e)
        else:
            p["fila_interior"].append(e)
        if zona_nom not in p["zones"]:
            p["zones"].append(zona_nom)
        p["unitats_palet"] += uni
        p["pes_acumulat"]  += pes
        p["total_caixes"]  += caixes
        p["n_clients"]     += 1

    def palet_preferit_zona(zona_nom, uni_necessari):
        """
        Tria el palet on posar un client, prioritzant:
          1. Palets que ja tenen clients de la MATEIXA ZONA
             (es descarreguen al mateix lloc)
          2. Si no, el palet amb més espai lliure
        """
        # Opció 1: palets que ja tenen la zona i tenen espai
        mateixa_zona = [p for p in palets
                        if zona_nom in p["zones"]
                        and espai_lliure(p) > 0.001]
        if mateixa_zona:
            return max(mateixa_zona, key=espai_lliure)

        # Opció 2: qualsevol palet amb espai
        amb_espai = [p for p in palets if espai_lliure(p) > 0.001]
        if amb_espai:
            return max(amb_espai, key=espai_lliure)

        return None  # Tot ple

    def distribuir_client(cr, fila):
        """
        Col·loca un client al palet usant la capacitat total (no 50/50).
        Exterior/interior és l'etiqueta d'ordre d'accés (qui agafa primer),
        però limitem la càrrega per banda a `cap_per_banda_max` perquè
        un palet no es carregui completament a una banda i caigui físicament.
        Si la fila preferida es queda sense espai, omple l'altra banda
        del MATEIX palet abans de saltar al següent.
        """
        uni_total = float(cr["unitats_palet"])
        pes_total = float(cr["pes_kg"])
        caixes    = int(cr["total_caixes"])
        cat       = cat_dominant(cr)
        r_pes     = pes_total / uni_total if uni_total > 0 else 0
        r_caixes  = caixes    / uni_total if uni_total > 0 else 0

        uni_restant = uni_total
        # Files a provar per ordre: primer la preferida, després l'oposada
        fila_oposada = "interior" if fila == "exterior" else "exterior"

        # Guàrdia per evitar bucles infinits si tot està ple
        max_iter = 4 * len(palets)
        it = 0

        while uni_restant > 0.001 and it < max_iter:
            it += 1
            dest = palet_preferit_zona(cr["zona_nom"], uni_restant)
            if dest is None:
                break  # Tots els palets completament plens

            # Provar primer la fila preferida del client
            collocat = False
            for f_intent in (fila, fila_oposada):
                espai = espai_lliure_fila(dest, f_intent)
                if espai <= 0.001:
                    continue
                uni_frag    = min(uni_restant, espai)
                pes_frag    = round(r_pes    * uni_frag, 1)
                caixes_frag = max(1, round(r_caixes * uni_frag))
                afegir_entrada(
                    dest,
                    cr["client"], cr["zona_id"], cr["zona_nom"],
                    cr["carrer"], cr["poblacio"],
                    uni_frag, pes_frag, caixes_frag, int(cr["n_linies"]),
                    cr["productes"], cr["quantitats"], cr["descripcions"],
                    cat, f_intent
                )
                uni_restant -= uni_frag
                collocat = True
                if uni_restant <= 0.001:
                    break

            # Si cap fila d'aquest palet ha tingut espai però el palet
            # encara té espai global, vol dir que les dues bandes estan
            # al límit per banda. Forcem usar la fila amb més espai relatiu.
            if not collocat and espai_lliure(dest) > 0.001:
                f_millor = max((fila, fila_oposada),
                               key=lambda f: cap_palet - usat_fila(dest, f))
                espai = max(0.0, cap_palet - dest["unitats_palet"])
                uni_frag    = min(uni_restant, espai)
                if uni_frag > 0.001:
                    pes_frag    = round(r_pes    * uni_frag, 1)
                    caixes_frag = max(1, round(r_caixes * uni_frag))
                    afegir_entrada(
                        dest,
                        cr["client"], cr["zona_id"], cr["zona_nom"],
                        cr["carrer"], cr["poblacio"],
                        uni_frag, pes_frag, caixes_frag, int(cr["n_linies"]),
                        cr["productes"], cr["quantitats"], cr["descripcions"],
                        cat, f_millor
                    )
                    uni_restant -= uni_frag

    # ── Furgoneta: banda única per palet (no hi ha laterals) ──
    # Camió: primera parada a exterior, resta a interior
    def fila_per_client(i):
        """Determina la fila on va el client i-èsim d'una zona."""
        if es_furgoneta:
            return "exterior"  # Tot va a una sola banda (no hi ha divisió)
        return "exterior" if i == 0 else "interior"

    # ── Iterar les zones en ordre de ruta (ve de P2 o baseline) ──
    for _, zona_row in per_zona.iterrows():

        # Clients de la zona ordenats: pesants primer (van a baix del palet)
        zona_clients = per_client[
            per_client["zona_id"] == zona_row["zona_id"]
        ].copy()
        zona_clients["_cat"] = zona_clients.apply(
            lambda r: min(
                categoria_producte(m, d, u)[0]
                for m, d, u in zip(
                    r["productes"], r["descripcions"],
                    r.get("ums") or ["CAJ"] * len(r["productes"])
                )
            ) if r["productes"] else 3,
            axis=1
        )
        zona_clients = zona_clients.sort_values(
            ["_cat", "unitats_palet"], ascending=[True, False]
        )

        uni_zona = float(zona_row["unitats_palet_total"])
        pes_zona = float(zona_row["pes_total_kg"])

        # INTENT 1: Tota la zona cap en un sol palet (ideal)
        palet_zona = next(
            (p for p in palets
             if espai_lliure(p) >= uni_zona
             and p["pes_acumulat"] + pes_zona <= pes_max),
            None
        )

        if palet_zona is not None:
            # Zona sencera al mateix palet
            for i, (_, cr) in enumerate(zona_clients.iterrows()):
                afegir_entrada(
                    palet_zona,
                    cr["client"], cr["zona_id"], cr["zona_nom"],
                    cr["carrer"], cr["poblacio"],
                    float(cr["unitats_palet"]), float(cr["pes_kg"]),
                    int(cr["total_caixes"]), int(cr["n_linies"]),
                    cr["productes"], cr["quantitats"], cr["descripcions"],
                    cat_dominant(cr),
                    fila_per_client(i)
                )
        else:
            # INTENT 2: Distribuir client per client
            for i, (_, cr) in enumerate(zona_clients.iterrows()):
                distribuir_client(cr, fila_per_client(i))

    # ── Nota sobre retornables ──
    # Els retornables (envasos buits) NO necessiten palet propi.
    # Van als espais lliures dels palets existents.
    # El transportista controla quants en recull per no sobrepassar
    # la capacitat del vehicle. Els camions s'obren per lateral i cua.

    # ── Cas especial: Furgoneta (accés posterior, porta del darrere) ──
    # La furgoneta NO té lona lateral → accés LIFO obligatori
    # Cal invertir l'ordre dels palets: l'última parada al fons
    if acces == "posterior":
        print("  Furgoneta: aplicant ordre LIFO (acces posterior)")
        palets.reverse()            # Invertir: última parada al fons
        for i, p in enumerate(palets):
            p["palet_num"] = i + 1  # Renumerar

    # ── Arrodonir valors i calcular % d'ocupació ──
    for p in palets:
        p["unitats_palet"] = round(p["unitats_palet"], 2)
        p["pes_acumulat"] = round(p["pes_acumulat"], 1)
        p["ocupacio_pct"] = round(
            p["unitats_palet"] / cap_palet * 100, 1
        ) if "tipus" not in p else 0

    # ── Mostrar resum ──
    print("\n  Resum assignacio:")
    for p in palets:
        if "tipus" in p:
            print(f"    Palet {p['palet_num']}: RETORNABLES")
        else:
            n_ext = len(p["fila_exterior"])
            n_int = len(p["fila_interior"])
            zones_txt = ", ".join(p["zones"][:3])
            print(
                f"    Palet {p['palet_num']}: "
                f"{p['n_clients']} clients "
                f"(ext:{n_ext} int:{n_int}) | "
                f"{p['unitats_palet']:.1f}/{cap_palet}u "
                f"({p['ocupacio_pct']}%) | {zones_txt}"
            )

    return palets


# ─────────────────────────────────────────────────────
# 7. GUARDAR JSON
# ─────────────────────────────────────────────────────

def guardar_layout(palets, vehicle_tipus, ruta_id,
                   path_out="layout_camio.json"):
    """
    Guarda l'assignació de palets en format JSON.
    Aquest fitxer és el lliurable per a P4 (Streamlit).

    Estructura del JSON:
      {
        "ruta": "DR0027",
        "vehicle": "camio_mitja",
        "palets": [
          {
            "palet_num": 1,
            "fila_exterior": [...],  <- clients accessibles des de la lona
            "fila_interior": [...],  <- clients al fons del palet
            ...
          }
        ],
        "resum": { totals agregats }
      }
    """
    v = VEHICLES[vehicle_tipus]
    layout = {
        "ruta":         ruta_id,
        "vehicle":      vehicle_tipus,
        "n_palets":     v["palets"],
        "vol_palet_m3": v["vol_palet_m3"],
        "acces":        v["acces"],
        "palets":       palets,
        "resum": {
            "total_clients":       sum(p.get("n_clients", 0) for p in palets),
            "total_caixes":        sum(p.get("total_caixes", 0) for p in palets),
            "total_unitats_palet": round(sum(p["unitats_palet"] for p in palets), 2),
            "total_pes_kg":        round(sum(p["pes_acumulat"] for p in palets), 1),
        },
    }

    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)

    r = layout["resum"]
    print(f"\nLayout guardat: {path_out}")
    print(f"  Clients: {r['total_clients']} | "
          f"Caixes: {r['total_caixes']} | "
          f"Unitats palet: {r['total_unitats_palet']} | "
          f"Pes: {r['total_pes_kg']}kg")
    return layout


# ─────────────────────────────────────────────────────
# 8. VISUALITZACIÓ PLOTLY
# ─────────────────────────────────────────────────────

def visualitzar_camio(palets, vehicle_tipus, ruta_id, data=None):
    """
    Genera DOS diagrames en el mateix HTML:

    1. VISTA MOSSO DE MAGATZEM (com carregar):
       - Vista de dalt del camió
       - Cada palet = un rectangle amb dos columnes (interior/exterior)
       - Barres VERTICALS dins cada columna = clients
         alçada proporcional a les unitats de palet
       - Colors per capa de pes (baix → dalt):
           Marró = barrils/CO2 (van a baix)
           Verd  = cervesa vidre
           Blau  = aigues/refrescos
           Groc  = secs/lleugers (van a dalt)
       - Mostra ordre de càrrega: 1r palet = fons del camió

    2. VISTA REPARTIDOR (com descarregar):
       - Llista ordenada per ordre de visita
       - Quin palet, quina banda, quin client
       - Informació pràctica: caixes, zona, adreça
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        import plotly.express as px
    except ImportError:
        print("Plotly no instal·lat: pip install plotly")
        return None

    v         = VEHICLES[vehicle_tipus]
    n_palets  = v["palets"]
    cap_palet = CAPACITAT_PALET[vehicle_tipus]
    mig_palet = cap_palet / 2

    # Colors per zona geogràfica
    colors    = px.colors.qualitative.Set2 + px.colors.qualitative.Pastel1
    all_zones = []
    for p in palets:
        for z in p.get("zones", []):
            if z not in all_zones:
                all_zones.append(z)
    color_map = {z: colors[i % len(colors)] for i, z in enumerate(all_zones)}

    # Colors per capa de pes (ordre d'apilament)
    CAPA_COLORS = {
        1: "#8B6355",   # Marró   = barrils/CO2 (van sempre a baix)
        2: "#4CAF79",   # Verd    = cervesa vidre (al mig-baix)
        3: "#5B9BD5",   # Blau    = aigues/refrescos (al mig)
        4: "#F0C040",   # Groc    = secs i lleugers (van a dalt)
    }
    CAPA_LABELS = {
        1: "Barrils/CO2 (BASE — sempre a baix)",
        2: "Cervesa vidre (MIG-BAIX)",
        3: "Aigues/refrescos (MIG)",
        4: "Secs i lleugers (DALT — mai res pesant a sobre)",
    }

    # ════════════════════════════════════════════════════
    # FIGURA 1: VISTA MOSSO DE MAGATZEM
    # ════════════════════════════════════════════════════
    n_cols = 2
    n_rows = (n_palets + n_cols - 1) // n_cols
    cel_w  = 3.0    # Amplada cel·la
    cel_h  = 3.5    # Alçada cel·la (més alta per mostrar piles)
    gap    = 0.30

    fig_mosso = go.Figure()

    for idx, palet in enumerate(palets):
        col  = idx % n_cols
        row  = idx // n_cols
        x0   = col * (cel_w + gap)
        y0   = (n_rows - 1 - row) * (cel_h + gap)
        x1   = x0 + cel_w
        y1   = y0 + cel_h
        xc   = (x0 + x1) / 2
        mig_x = x0 + cel_w / 2

        # Marc del palet
        fig_mosso.add_shape(
            type="rect", x0=x0, y0=y0, x1=x1, y1=y1,
            line=dict(color="#333333", width=2),
            fillcolor="#fafafa"
        )

        # Línia divisòria interior/exterior
        fig_mosso.add_shape(
            type="line",
            x0=mig_x, y0=y0+0.05,
            x1=mig_x, y1=y1-0.50,
            line=dict(color="#bbbbbb", width=1.5, dash="dot")
        )

        # Etiquetes de banda (només per camió amb laterals)
        if v["acces"] != "posterior":
            fig_mosso.add_annotation(
                x=(x0+mig_x)/2, y=y1-0.28,
                text="◀ INTERIOR<br><small>(fons palet)</small>",
                showarrow=False, font=dict(size=8, color="#888"),
                align="center"
            )
            fig_mosso.add_annotation(
                x=(mig_x+x1)/2, y=y1-0.28,
                text="EXTERIOR ▶<br><small>(banda lona)</small>",
                showarrow=False, font=dict(size=8, color="#888"),
                align="center"
            )

        # Dibuixar contingut del palet
        if v["acces"] == "posterior":
            # FURGONETA: una sola columna (tot el palet, sense divisió lateral)
            all_items = sorted(
                palet.get("clients", []),
                key=lambda c: c.get("capa_pes", 3),
                reverse=False
            )
            y_max_content = y1 - 0.54
            y_base        = y0 + 0.06
            h_disponible  = y_max_content - y_base
            total_uni     = sum(c["unitats_palet"] for c in all_items)
            escala        = h_disponible / max(total_uni, 0.001) if total_uni > 0 else 1
            y_actual      = y_base
            x_start       = x0 + 0.06
            x_end         = x1 - 0.06

            for cli in all_items:
                uni    = cli["unitats_palet"]
                h_frag = uni * escala
                h_frag = max(h_frag, 0.15)
                if y_actual + h_frag > y_max_content:
                    h_frag = max(0, y_max_content - y_actual)
                if h_frag < 0.01:
                    continue
                cat    = cli.get("capa_pes", 3)
                color  = CAPA_COLORS.get(cat, "#aaaaaa")

                fig_mosso.add_shape(
                    type="rect",
                    x0=x_start, y0=y_actual,
                    x1=x_end,   y1=y_actual + h_frag,
                    fillcolor=color,
                    line=dict(color="white", width=1.5),
                    opacity=0.85
                )
                if h_frag > 0.25:
                    nom = (cli["client"][:18] + "…"
                           if len(cli["client"]) > 18
                           else cli["client"])
                    fig_mosso.add_annotation(
                        x=(x_start + x_end) / 2,
                        y=y_actual + h_frag / 2,
                        text=f"<b>{nom}</b><br>{cli['total_caixes']}cx · {uni:.0f}u",
                        showarrow=False,
                        font=dict(size=7.5, color="white"),
                        align="center"
                    )
                y_actual += h_frag
        else:
            # CAMIÓ: dues bandes (exterior/interior) com a PILES VERTICALS
            # Alçada proporcional al total del palet (no a la meitat)
            y_max_content = y1 - 0.54  # Límit superior (deixar espai per capçalera)
            y_base        = y0 + 0.06  # Límit inferior
            h_disponible  = y_max_content - y_base

            for banda, fila_key, x_start, x_end in [
                ("exterior", "fila_exterior", mig_x + 0.06, x1 - 0.06),
                ("interior", "fila_interior", x0 + 0.06,   mig_x - 0.06),
            ]:
                items = sorted(
                    palet.get(fila_key, []),
                    key=lambda c: c.get("capa_pes", 3),
                    reverse=False
                )
                # Total d'unitats en aquesta banda (per escalar proporcionalment)
                total_banda = sum(c["unitats_palet"] for c in items)
                escala = h_disponible / max(total_banda, 0.001) if total_banda > 0 else 1
                y_actual = y_base

                for cli in items:
                    uni    = cli["unitats_palet"]
                    h_frag = uni * escala
                    h_frag = max(h_frag, 0.15)
                    # Clampar perquè no surti del marc
                    if y_actual + h_frag > y_max_content:
                        h_frag = max(0, y_max_content - y_actual)
                    if h_frag < 0.01:
                        continue
                    cat    = cli.get("capa_pes", 3)
                    color  = CAPA_COLORS.get(cat, "#aaaaaa")

                    fig_mosso.add_shape(
                        type="rect",
                        x0=x_start, y0=y_actual,
                        x1=x_end,   y1=y_actual + h_frag,
                        fillcolor=color,
                        line=dict(color="white", width=1.5),
                        opacity=0.85 if banda == "exterior" else 0.55
                    )
                    if h_frag > 0.25:
                        nom = (cli["client"][:18] + "…"
                               if len(cli["client"]) > 18
                               else cli["client"])
                        fig_mosso.add_annotation(
                            x=(x_start + x_end) / 2,
                            y=y_actual + h_frag / 2,
                            text=f"<b>{nom}</b><br>{cli['total_caixes']}cx · {uni:.0f}u",
                            showarrow=False,
                            font=dict(size=7.5, color="white"),
                            align="center"
                        )
                    y_actual += h_frag

        # Capçalera del palet
        oc  = palet["ocupacio_pct"]
        bar = "█" * min(int(oc/10), 10) + "░" * max(10-int(oc/10), 0)
        zones_txt = ", ".join(palet["zones"][:2])
        fig_mosso.add_annotation(
            x=xc, y=y1 - 0.14,
            text=f"<b>Palet {palet['palet_num']} · {oc:.0f}%</b>  {bar}<br>{zones_txt}",
            showarrow=False,
            font=dict(size=9, color="#222"), align="center",
            bgcolor="rgba(255,255,255,0.95)",
            bordercolor="#ccc", borderwidth=1, borderpad=3
        )

    # Etiquetes vehicle
    total_w = n_cols * (cel_w + gap)
    if v["acces"] == "posterior":
        # Furgoneta: accés només pel darrere
        fig_mosso.add_annotation(
            x=total_w/2, y=n_rows*(cel_h+gap)+0.20,
            text="⬛  CABINA (fons furgoneta) — carregar primer  ⬛",
            showarrow=False, font=dict(size=13, color="#333")
        )
        fig_mosso.add_annotation(
            x=total_w/2, y=-0.40,
            text="🚪  PORTA DEL DARRERE — descarregar per aquí  🚪",
            showarrow=False, font=dict(size=13, color="#333")
        )
    else:
        # Camió: accés lateral per lona
        fig_mosso.add_annotation(
            x=total_w/2, y=n_rows*(cel_h+gap)+0.20,
            text="⬛  FONS DEL CAMIÓ — carregar primer  ⬛",
            showarrow=False, font=dict(size=13, color="#333")
        )
        fig_mosso.add_annotation(
            x=total_w/2, y=-0.40,
            text="🚪  PORTA LATERAL (lona)  🚪",
            showarrow=False, font=dict(size=13, color="#333")
        )

    # Llegenda capes de pes
    for cat, color in CAPA_COLORS.items():
        fig_mosso.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=14, color=color, symbol="square"),
            name=CAPA_LABELS[cat], showlegend=True
        ))

    fig_mosso.update_layout(
        title=dict(
            text=(f"📦 PLA DE CÀRREGA — {ruta_id}"
                  + (f" · {data}" if data else "")
                  + f"  |  VISTA MOSSO DE MAGATZEM"),
            font=dict(size=14)
        ),
        xaxis=dict(visible=False, range=[-0.2, total_w + 0.2]),
        yaxis=dict(visible=False, range=[-0.6, n_rows*(cel_h+gap)+0.5]),
        plot_bgcolor="white", paper_bgcolor="white",
        width=880, height=150 + n_rows * 340,
        legend=dict(
            title="<b>Ordre d'apilament (baix → dalt)</b>",
            font=dict(size=9), x=1.02, y=1
        ),
        margin=dict(l=20, r=280, t=60, b=55),
    )

    # ════════════════════════════════════════════════════
    # FIGURA 2: VISTA REPARTIDOR
    # ════════════════════════════════════════════════════
    # Llista senzilla per ordre de visita: zona → palet → banda → caixes
    fig_rep = go.Figure()

    # Construir taula de parades
    taula_zones = {}
    for p in palets:
        for cli in p.get("clients", []):
            zona = cli["zona_nom"]
            if zona not in taula_zones:
                taula_zones[zona] = []
            taula_zones[zona].append({
                "palet":   p["palet_num"],
                "banda":   "🔓 LONA" if cli["fila"] == "exterior" else "🔒 FONS",
                "client":  cli["client"],
                "caixes":  cli["total_caixes"],
                "carrer":  cli.get("carrer", ""),
                "poblacio": cli.get("poblacio", ""),
            })

    # Crear taula visual
    files_zona, files_palet, files_banda, files_client, files_caixes, files_adreca = [], [], [], [], [], []
    for zona, items in taula_zones.items():
        for it in sorted(items, key=lambda x: (x["palet"], x["banda"])):
            files_zona.append(zona)
            files_palet.append(f"Palet {it['palet']}")
            files_banda.append(it["banda"])
            files_client.append(it["client"])
            files_caixes.append(str(it["caixes"]))
            files_adreca.append(f"{it['carrer']}, {it['poblacio']}")

    fig_rep.add_trace(go.Table(
        header=dict(
            values=["<b>Zona</b>", "<b>Palet</b>", "<b>Banda</b>",
                    "<b>Client</b>", "<b>Caixes</b>", "<b>Adreça</b>"],
            fill_color="#2c3e50",
            font=dict(color="white", size=11),
            align="left", height=32
        ),
        cells=dict(
            values=[files_zona, files_palet, files_banda,
                    files_client, files_caixes, files_adreca],
            fill_color=[
                ["#f0f4f8" if i % 2 == 0 else "white"
                 for i in range(len(files_zona))]
            ] * 6,
            font=dict(size=10),
            align="left", height=28
        )
    ))

    fig_rep.update_layout(
        title=dict(
            text=f"🚛 ORDRE DE REPARTIMENT — {ruta_id}"
                 + (f" · {data}" if data else "")
                 + "  |  VISTA REPARTIDOR",
            font=dict(size=14)
        ),
        width=880,
        height=max(400, 80 + len(files_zona) * 32),
        margin=dict(l=20, r=20, t=55, b=20),
    )

    # Guardar tots dos en un sol HTML
    with open("layout_camio.html", "w", encoding="utf-8") as f:
        f.write("<html><head><meta charset='utf-8'>")
        f.write("<style>body{font-family:Arial,sans-serif;padding:20px;background:#f5f5f5;}")
        f.write("h2{color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:8px;}</style></head><body>")
        f.write("<h2>📦 Pla de càrrega del camió</h2>")
        f.write(fig_mosso.to_html(full_html=False, include_plotlyjs="cdn"))
        f.write("<br><hr><br>")
        f.write("<h2>🚛 Ordre de repartiment per al repartidor</h2>")
        f.write(fig_rep.to_html(full_html=False, include_plotlyjs=False))
        f.write("</body></html>")

    print("\nVisualitzacio: layout_camio.html")
    print("  - Vista mosso de magatzem (com carregar)")
    print("  - Vista repartidor (ordre de descàrrega)")
    try:
        fig_mosso.show()
    except Exception:
        pass
    return fig_mosso
# ─────────────────────────────────────────────────────
# 9. FUNCIÓ PRINCIPAL
# ─────────────────────────────────────────────────────

def main():
    """
    Executa el pipeline complet d'optimització de càrrega:
      1. Carregar dades (Hackaton.xlsx + ZM040.xlsx)
      2. Preparar dimensions de productes (ZM040)
      3. Filtrar ruta i calcular volums per línia de comanda
      4. Agregar per client i zona geogràfica
      5. Carregar ordre de ruta de P2 (o usar baseline)
      6. Assignar clients a palets (fila exterior/interior)
      7. Guardar JSON per a P4/Streamlit
    """
    print("=" * 50)
    print("  DAMM ROUTES - Optimitzacio de carrega")
    print("=" * 50)

    df, zm           = carregar_dades()
    dims             = preparar_dimensions(zm)
    comandes, _      = preparar_ruta(
        df, dims, RUTA_OBJECTIU, DATA_OBJECTIU,
        client_ids=CLIENT_IDS_OBJECTIU,
    )
    per_client, per_zona = agregar_per_client(comandes)
    per_zona         = carregar_ordre_ruta(per_zona)

    # Selecció automàtica del vehicle segons volum i pes
    vehicle          = triar_vehicle(per_zona)

    palets           = assignar_carrega(per_client, per_zona, vehicle)
    layout           = guardar_layout(palets, vehicle, RUTA_OBJECTIU)
    # Visualització HTML desactivada: ja generem els PDFs al pas 04.
    # Si la vols recuperar, descomenta la línia següent:
    # visualitzar_camio(palets, vehicle, RUTA_OBJECTIU, DATA_OBJECTIU)

    print("\nTot llest!")
    print("  -> layout_camio.json  (per a P4 / Streamlit)")
    return layout

if __name__ == "__main__":
    layout = main()