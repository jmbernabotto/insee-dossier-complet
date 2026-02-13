import streamlit as st
import pandas as pd
import requests
import geopandas as gpd
import folium
import json
import numpy as np
from streamlit_folium import st_folium
import io

st.set_page_config(page_title="INSEE Finder", layout="wide")

INSEE_KEY = st.secrets.get("INSEE_API_KEY", "dfc20306-246c-477c-8203-06246c977cba")

@st.cache_data
def load_insee(endpt):
    h = {"Authorization": f"Bearer {INSEE_KEY}", "Accept": "application/json"}
    try:
        r = requests.get(f"https://api.insee.fr/metadonnees/geo/{endpt}", headers=h)
        return r.json() if r.status_code == 200 else []
    except: return []

@st.cache_data
def get_geo(code, kind, name):
    # Priorité API Géo Etalab
    m = {"EPCI": "epcis", "communes": "communes", "departements": "departements", "regions": "regions"}
    if kind in m:
        try:
            r = requests.get(f"https://geo.api.gouv.fr/{m[kind]}/{code}?format=geojson&geometry=contour")
            if r.status_code == 200:
                gdf = gpd.read_file(io.StringIO(r.text))
                if not gdf.empty: return gdf
        except: pass
    return None

st.title("🗺️ Test Cartographie")

type_col = st.sidebar.selectbox("Type", ["communes", "EPCI", "departements", "regions"])
data = load_insee("intercommunalites" if type_col == "EPCI" else type_col)

if data:
    df = pd.DataFrame(data)
    c_col = 'code'
    t_col = 'intituleComplet' if 'intituleComplet' in df.columns else 'intitule'
    df = df.rename(columns={c_col: 'CODE', t_col: 'TITLE'})
    df['CODE'] = df['CODE'].astype(str)
    if type_col == "EPCI": df['CODE'] = df['CODE'].str.zfill(9)
    
    search = st.sidebar.text_input("Rechercher")
    if search:
        res = df[df['TITLE'].str.contains(search, case=False) | df['CODE'].str.contains(search)].head(10)
        if not res.empty:
            sel = st.sidebar.selectbox("Choisir", res['TITLE'].tolist())
            row = res[res['TITLE'] == sel].iloc[0]
            
            gdf = get_geo(row['CODE'], type_col, row['TITLE'])
            
            col1, col2 = st.columns([1, 2])
            col1.metric("Territoire", row['TITLE'])
            col1.write(f"Code : {row['CODE']}")

            # Lien vers le dossier INSEE
            prefix = "EPCI" if type_col == "EPCI" else ("COM" if type_col == "communes" else ("DEP" if type_col == "departements" else "REG"))
            url_insee = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{row['CODE']}"
            col1.link_button("📄 Voir le dossier INSEE", url_insee, use_container_width=True)
            
            if gdf is not None:
                with col2:
                    # Calcul du centre
                    center = gdf.to_crs(epsg=3857).centroid.to_crs(epsg=4326).iloc[0]
                    m = folium.Map(location=[center.y, center.x], zoom_start=9)
                    
                    # Nettoyage des colonnes pour éviter l'erreur ndarray/JSON
                    for col in gdf.columns:
                        if col != 'geometry':
                            gdf[col] = gdf[col].astype(str)
                    
                    # Conversion sécurisée
                    geojson_data = json.loads(gdf.to_json())
                    folium.GeoJson(geojson_data).add_to(m)
                    
                    st_folium(m, width=700, height=500, returned_objects=[])
            else:
                col1.error("Contour non trouvé")
