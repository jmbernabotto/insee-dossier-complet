import streamlit as st
import pynsee
import pandas as pd
import requests
import unidecode
import folium
from streamlit_folium import folium_static
import geopandas as gpd
import re

# Récupération de la clé API (Local ou Cloud)
if "INSEE_API_KEY" in st.secrets:
    API_KEY = st.secrets["INSEE_API_KEY"]
else:
    API_KEY = "dfc20306-246c-477c-8203-06246c977cba"

st.set_page_config(page_title="INSEE Geo Finder", page_icon="🗺️", layout="wide")
st.title("🗺️ Recherche & Cartographie INSEE")

@st.cache_data(show_spinner=False)
def load_data(area_type):
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    try:
        if area_type == "EPCI":
            url = "https://api.insee.fr/metadonnees/geo/intercommunalites"
            resp = requests.get(url, headers=headers)
            if resp.status_code == 200:
                df = pd.DataFrame(resp.json())
                df = df.rename(columns={'code': 'CODE', 'intituleComplet': 'TITLE'})
                df['DISPLAY_TITLE'] = df['TITLE'] + " (" + df['CODE'] + ")"
                return df
        elif area_type == "communes":
            url = "https://api.insee.fr/metadonnees/geo/communes"
            resp = requests.get(url, headers=headers)
            if resp.status_code == 200:
                df = pd.DataFrame(resp.json())
                df = df.rename(columns={'code': 'CODE', 'intitule': 'TITLE'})
                df['DISPLAY_TITLE'] = df['TITLE'] + " (" + df['CODE'] + ")"
                return df
        df = pynsee.get_area_list(area_type)
        if not df.empty:
            df['DISPLAY_TITLE'] = df['TITLE'] + " (" + df['CODE'] + ")"
        return df
    except Exception: return pd.DataFrame()

@st.cache_data(show_spinner="Extraction des chiffres clés...")
def get_key_metrics(area_type, code):
    """Récupère les indicateurs clés via pynsee"""
    try:
        # Mapping des niveaux géographiques pour pynsee
        nivgeo_map = {
            "communes": "COM",
            "EPCI": "EPCI",
            "departements": "DEP",
            "regions": "REG"
        }
        nivgeo = nivgeo_map.get(area_type)
        if not nivgeo: return None

        # On récupère les données de population et d'activité
        # Dataset : GEO2023RP2020 (Recensement 2020 sur géo 2023)
        data = pynsee.get_local_data(
            variables=["POPULATION", "NB_ENTR_Secteur_A", "NB_ENTR_Secteur_B", "NB_ENTR_Secteur_C", "NB_ENTR_Secteur_D", "NB_ENTR_Secteur_E"],
            nivgeo=nivgeo,
            geocodes=[code]
        )
        
        if data is not None and not data.empty:
            # Extraction des valeurs
            pop = data[data['VARIABLE'] == 'POPULATION']['OBS_VALUE'].values[0]
            # Somme des entreprises
            ent_vars = ["NB_ENTR_Secteur_A", "NB_ENTR_Secteur_B", "NB_ENTR_Secteur_C", "NB_ENTR_Secteur_D", "NB_ENTR_Secteur_E"]
            ent = data[data['VARIABLE'].isin(ent_vars)]['OBS_VALUE'].astype(float).sum()
            
            return {
                "population": int(float(pop)),
                "entreprises": int(ent)
            }
    except: pass
    return None

@st.cache_data(show_spinner="Identification de l'EPCI...")
def get_epci_from_commune(com_code):
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    url = f"https://api.insee.fr/metadonnees/geo/commune/{com_code}/ascendants?type=Intercommunalite"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            if data:
                return {'CODE': data[0]['code'], 'TITLE': data[0]['intitule']}
    except: pass
    return None

@st.cache_data(show_spinner=False)
def get_geometry_robust(area_type, code, name):
    try:
        geo_map = {"communes": "ADMINEXPRESS-COG-CARTO.LATEST:commune", "EPCI": "ADMINEXPRESS-COG-CARTO.LATEST:epci", "departements": "ADMINEXPRESS-COG-CARTO.LATEST:departement", "regions": "ADMINEXPRESS-COG-CARTO.LATEST:region"}
        gdf = pynsee.get_geodata(geo_map.get(area_type, ""))
        code_col = 'code_siren' if area_type == "EPCI" else 'code_insee'
        if code_col not in gdf.columns:
            for alt in ['code', 'insee_com']:
                if alt in gdf.columns: code_col = alt; break
        gdf_filtered = gdf[gdf[code_col] == code]
        if not gdf_filtered.empty: return gdf_filtered.to_crs("EPSG:4326")
    except: pass
    return None

def normalize_text(text):
    return unidecode.unidecode(str(text)).lower()

categories = {
    "Administratif": {"Communes": "communes", "Intercommunalités (EPCI)": "EPCI", "Départements": "departements", "Régions": "regions"},
    "Zonages d'étude": {"Aires d'attraction (2020)": "airesDAttractionDesVilles2020", "Unités Urbaines (2020)": "unitesUrbaines2020", "Zones d'emploi (2020)": "zonesDEmploi2020", "Bassins de vie (2022)": "bassinsDeVie2022"}
}

st.sidebar.title("🛠️ Paramètres")
cat_selected = st.sidebar.selectbox("Catégorie", list(categories.keys()))
type_label = st.sidebar.selectbox("Type", list(categories[cat_selected].keys()))
area_key = categories[cat_selected][type_label]

df_list = load_data(area_key)

if not df_list.empty:
    st.sidebar.markdown("---")
    final_code, final_name = None, None
    
    if area_key == "EPCI":
        search_mode = st.sidebar.radio("Chercher par :", ["Nom de l'EPCI", "Commune membre"])
        search_query = st.sidebar.text_input("🔍 Saisie", placeholder="Vernon, Agglopolys...")
        if search_query:
            if search_mode == "Nom de l'EPCI":
                mask = df_list['TITLE'].str.contains(search_query, case=False, na=False) | df_list['CODE'].str.contains(search_query)
                results = df_list[mask].head(100)
                if not results.empty:
                    sel_display = st.sidebar.selectbox("EPCI", results['DISPLAY_TITLE'].tolist())
                    row_sel = results[results['DISPLAY_TITLE'] == sel_display].iloc[0]
                    final_code, final_name = row_sel['CODE'], row_sel['TITLE']
            else:
                df_com = load_data("communes")
                mask_com = df_com['TITLE'].str.contains(search_query, case=False, na=False) | df_com['CODE'].str.contains(search_query)
                com_results = df_com[mask_com].head(100)
                if not com_results.empty:
                    sel_com_display = st.sidebar.selectbox("Commune", com_results['DISPLAY_TITLE'].tolist())
                    com_code = com_results[com_results['DISPLAY_TITLE'] == sel_com_display].iloc[0]['CODE']
                    epci_info = get_epci_from_commune(com_code)
                    if epci_info:
                        final_code, final_name = epci_info['CODE'], epci_info['TITLE']
    else:
        search_query = st.sidebar.text_input("🔍 Rechercher", placeholder="Nom ou Code...")
        if search_query:
            df_list['norm_title'] = df_list['TITLE'].apply(normalize_text)
            mask = df_list.apply(lambda row: all(kw in row['norm_title'] for kw in normalize_text(search_query).split()) or search_query in str(row['CODE']), axis=1)
            results = df_list[mask].head(100)
            if not results.empty:
                sel_display = st.sidebar.selectbox("Résultat", results['DISPLAY_TITLE'].tolist())
                row_sel = results[results['DISPLAY_TITLE'] == sel_display].iloc[0]
                final_code, final_name = row_sel['CODE'], row_sel['TITLE']

    if final_code:
        # Affichage des chiffres clés
        metrics = get_key_metrics(area_key, final_code)
        if metrics:
            m1, m2, m3 = st.columns(3)
            m1.metric("Population totale", f"{metrics['population']:,}".replace(',', ' '))
            m2.metric("Nombre d'entreprises", f"{metrics['entreprises']:,}".replace(',', ' '))
            m3.metric("Niveau géo", area_key.upper())
        
        col_info, col_map = st.columns([1, 3])
        with col_info:
            st.subheader(final_name)
            prefixes = {"communes":"COM", "EPCI":"EPCI", "departements":"DEP", "regions":"REG", "airesDAttractionDesVilles2020":"AAV2020", "unitesUrbaines2020":"UU2020", "zonesDEmploi2020":"ZE2020", "bassinsDeVie2022":"BV2022"}
            st.link_button("📄 Dossier Complet INSEE", f"https://www.insee.fr/fr/statistiques/2011101?geo={prefixes.get(area_key, 'COM')}-{final_code}", use_container_width=True, type="primary")
            gdf = get_geometry_robust(area_key, final_code, final_name)
            if gdf is not None:
                st.success("✅ Contour chargé")
                st.download_button("📥 GeoJSON", gdf.to_json(), f"{final_code}.geojson")

        with col_map:
            if gdf is not None:
                centroid = gdf.geometry.centroid.iloc[0]
                m = folium.Map(location=[centroid.y, centroid.x], zoom_start=11, tiles=None)
                folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Satellite').add_to(m)
                folium.TileLayer('OpenStreetMap', name="Plan").add_to(m)
                folium.GeoJson(gdf, name="Limite", style_function=lambda x: {'fillColor': '#318ce7', 'color': 'black', 'weight': 2, 'fillOpacity': 0.3}).add_to(m)
                folium.LayerControl(collapsed=False).add_to(m)
                folium_static(m, width=1000, height=600)
else:
    st.error("Données indisponibles.")
