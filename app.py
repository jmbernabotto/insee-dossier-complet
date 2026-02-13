import streamlit as st
import pandas as pd
import requests
import geopandas as gpd
import folium
import json
import numpy as np
from streamlit_folium import st_folium
import io

st.set_page_config(page_title="Dossier INSEE", layout="wide")

INSEE_KEY = st.secrets.get("INSEE_API_KEY", "dfc20306-246c-477c-8203-06246c977cba")

@st.cache_data
def load_insee(endpt):
    h = {"Authorization": f"Bearer {INSEE_KEY}", "Accept": "application/json"}
    try:
        r = requests.get(f"https://api.insee.fr/metadonnees/geo/{endpt}", headers=h)
        if r.status_code == 200:
            return r.json()
        else:
            st.error(f"Erreur API INSEE {r.status_code} pour {endpt}")
            return []
    except Exception as e:
        st.error(f"Erreur de connexion INSEE : {e}")
        return []

@st.cache_data
def get_geo(code, kind, name):
    m = {"EPCI": "epcis", "communes": "communes", "departements": "departements", "regions": "regions"}
    if kind not in m: return None
    
    clean_code = str(code).strip()
    # Utilisation du point d'entrée collection avec filtre pour garantir une FeatureCollection
    url = f"https://geo.api.gouv.fr/{m[kind]}?code={clean_code}&format=geojson&geometry=contour"
    
    try:
        r = requests.get(url, headers={'User-Agent': 'DossierInseeApp/1.0'}, timeout=10)
        if r.status_code == 200:
            gdf = gpd.read_file(io.StringIO(r.text))
            if not gdf.empty: return gdf
        else:
            st.error(f"API Géo ({m[kind]}) : Erreur {r.status_code} pour le code {clean_code}")
    except Exception as e:
        st.error(f"Erreur technique Géo : {e}")
    return None

st.title("📊 Dossier INSEE")

type_col = st.sidebar.selectbox("Type", ["communes", "EPCI", "departements", "regions"])
data = load_insee("intercommunalites" if type_col == "EPCI" else type_col)

if data:
    df = pd.DataFrame(data)
    
    # Détection des colonnes
    possible_codes = ['code', 'codeRegion', 'codeDepartement', 'codeEpci']
    c_col = next((c for c in possible_codes if c in df.columns), df.columns[0])
    
    if 'intituleComplet' in df.columns:
        t_col = 'intituleComplet'
    elif 'intitule' in df.columns:
        t_col = 'intitule'
    else:
        t_cols = [c for c in df.columns if c != c_col]
        t_col = t_cols[0] if t_cols else c_col

    df = df.rename(columns={c_col: 'CODE', t_col: 'TITLE'})
    df['CODE'] = df['CODE'].astype(str).str.strip()
    
    # Padding
    if type_col == "EPCI": df['CODE'] = df['CODE'].str.zfill(9)
    elif type_col == "communes": df['CODE'] = df['CODE'].str.zfill(5)
    elif type_col in ["departements", "regions"]: df['CODE'] = df['CODE'].str.zfill(2)
    
    search = st.sidebar.text_input("Rechercher")
    if search:
        mask = df['TITLE'].str.contains(search, case=False, na=False) | df['CODE'].str.contains(search, na=False)
        res = df[mask].head(10)
        
        if not res.empty:
            sel = st.sidebar.selectbox("Choisir", res['TITLE'].tolist())
            row = res[res['TITLE'] == sel].iloc[0]
            
            gdf = get_geo(row['CODE'], type_col, row['TITLE'])
            
            st.header(row['TITLE'])
            
            col1, col2 = st.columns([1, 2])
            col1.metric("Territoire", row['TITLE'])
            col1.write(f"Code : {row['CODE']}")
            
            prefix = "EPCI" if type_col == "EPCI" else ("COM" if type_col == "communes" else ("DEP" if type_col == "departements" else "REG"))
            url_insee = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{row['CODE']}"
            col1.link_button("📄 Voir le dossier INSEE", url_insee, use_container_width=True)
            
            if gdf is not None:
                with col2:
                    center = gdf.to_crs(epsg=3857).centroid.to_crs(epsg=4326).iloc[0]
                    m = folium.Map(location=[center.y, center.x], zoom_start=7 if type_col in ["regions", "departements"] else 9)
                    
                    for col in gdf.columns:
                        if col != 'geometry':
                            gdf[col] = gdf[col].astype(str)
                    
                    geojson_data = json.loads(gdf.to_json())
                    folium.GeoJson(geojson_data).add_to(m)
                    st_folium(m, width=700, height=500, returned_objects=[])
        else:
            st.sidebar.warning("Aucun résultat.")
