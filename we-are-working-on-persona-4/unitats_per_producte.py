import pandas as pd

# 1. Carregar els fitxers Excel
df_entregues = pd.read_excel('Hackaton.xlsx', sheet_name='Detalle entrega')
df_dimensions = pd.read_excel('ZM040.XLSX')

# 2. Explorar els noms de les columnes (Descomenta aquestes línies per veure com es diuen les teves columnes)
print("Columnes Entregues:", df_entregues.columns.tolist())
print("Columnes Dimensions:", df_dimensions.columns.tolist())

# --- ATENCIÓ: Substitueix aquests noms pels reals dels teus Excels ---
COLUMNA_CODI_PRODUCTE_ENTREGUES = 'Material' # Ex: Codi de producte a entregues
COLUMNA_QUANTITAT = 'Cantidad entrega'        # Ex: Unitats demanades

COLUMNA_CODI_PRODUCTE_DIMENSIONS = 'Material' # Ex: Codi de producte a l'excel de dimensions
COLUMNA_VOLUM = 'Volumen'                     # Ex: Volum o espai que ocupa 1 unitat
# ----------------------------------------------------------------------

# 3. Netejar i agrupar les entregues (sumar les quantitats totals per cada producte)
entregues_agrupades = df_entregues.groupby(COLUMNA_CODI_PRODUCTE_ENTREGUES)[COLUMNA_QUANTITAT].sum().reset_index()

# 4. Creuar les dades (fer un "VLOOKUP" / "BuscarV" a l'estil Python)
df_final = pd.merge(
    entregues_agrupades, 
    df_dimensions[[COLUMNA_CODI_PRODUCTE_DIMENSIONS, COLUMNA_VOLUM]], # Només agafem codi i volum
    left_on=COLUMNA_CODI_PRODUCTE_ENTREGUES, 
    right_on=COLUMNA_CODI_PRODUCTE_DIMENSIONS, 
    how='left'
)

# 5. Calcular l'espai total ocupat per cada producte (Quantitat * Volum Unitari)
df_final['Volum_Total_Ocupat'] = df_final[COLUMNA_QUANTITAT] * df_final[COLUMNA_VOLUM]

# 6. Mostrar els resultats ordenats de més a menys volum ocupat
df_final = df_final.sort_values(by='Volum_Total_Ocupat', ascending=False)

print("Resum de l'espai ocupat per cada producte:")
print(df_final.head(10)) # Mostrem el Top 10 productes que ocupen més espai

# Opcional: Guardar el resultat en un nou CSV per compartir-ho amb els companys
df_final.to_csv('resultat_volums_ocupats.csv', index=False)