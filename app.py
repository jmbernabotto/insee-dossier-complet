import streamlit as st
import pynsee
import pandas as pd
import requests
import unidecode
import folium
from streamlit_folium import folium_static
import geopandas as gpd
import re

# Clé API INSEE
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
                return df.rename(columns={'code': 'CODE', 'intituleComplet': 'TITLE'})
        elif area_type == "communes":
            url = "https://api.insee.fr/metadonnees/geo/communes"
            resp = requests.get(url, headers=headers)
            if resp.status_code == 200:
                df = pd.DataFrame(resp.json())
                df['DISPLAY_TITLE'] = df['intitule'] + " (" + df['code'].str[:2] + ")"
                return df.rename(columns={'code': 'CODE', 'intitule': 'TITLE'})
        return pynsee.get_area_list(area_type)
    except Exception: return pd.DataFrame()

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
    
    if area_key == "EPCI":
        search_mode = st.sidebar.radio("Chercher par :", ["Nom de l'EPCI", "Commune membre"])
        search_query = st.sidebar.text_input("🔍 Saisie", placeholder="Vernon, Blois, Nantes...")
        
        if search_query:
            if search_mode == "Nom de l'EPCI":
                mask = df_list['TITLE'].str.contains(search_query, case=False, na=False)
                results = df_list[mask].head(20)
                if not results.empty:
                    sel_name = st.sidebar.selectbox("Résultat", results['TITLE'].tolist())
                    final_code = results[results['TITLE'] == sel_name].iloc[0]['CODE']
                    final_name = sel_name
                else:
                    st.sidebar.error("Aucun EPCI trouvé avec ce nom.")
                    final_code = None
            else:
                # Mode commune membre
                df_com = load_data("communes")
                mask_com = df_com['TITLE'].str.contains(search_query, case=False, na=False)
                com_results = df_com[mask_com].head(20)
                if not com_results.empty:
                    sel_com_display = st.sidebar.selectbox("Sélectionnez la commune", com_results['DISPLAY_TITLE'].tolist())
                    com_code = com_results[com_results['DISPLAY_TITLE'] == sel_com_display].iloc[0]['CODE']
                    epci_info = get_epci_from_commune(com_code)
                    if epci_info:
                        final_code = epci_info['CODE']
                        final_name = epci_info['TITLE']
                        st.sidebar.success(f"EPCI trouvé : {final_name}")
                    else:
                        st.sidebar.warning("Cette commune ne semble pas appartenir à un EPCI.")
                        final_code = None
                else:
                    st.sidebar.error("Commune non trouvée.")
                    final_code = None
        else: final_code = None
    else:
        # Recherche standard pour les autres catégories
        search_query = st.sidebar.text_input("🔍 Rechercher", placeholder="Nom ou Code...")
        if search_query:
            df_list['norm_title'] = df_list['TITLE'].apply(normalize_text)
            mask = df_list.apply(lambda row: all(kw in row['norm_title'] for kw in normalize_text(search_query).split()) or search_query in str(row['CODE']), axis=1)
            results = df_list[mask].head(20)
            if not results.empty:
                final_name = st.sidebar.selectbox("Résultat", results['TITLE'].tolist())
                final_code = results[results['TITLE'] == final_name].iloc[0]['CODE']
            else:
                st.sidebar.error("Aucun résultat.")
                final_code = None
        else: final_code = None

    if final_code:
        col_info, col_map = st.columns([1, 3])
        with col_info:
            st.subheader(final_name)
            st.metric("Identifiant", final_code)
            prefixes = {"communes":"COM", "EPCI":"EPCI", "departements":"DEP", "regions":"REG", "airesDAttractionDesVilles2020":"AAV2020", "unitesUrbaines2020":"UU2020", "zonesDEmploi2020":"ZE2020", "bassinsDeVie2022":"BV2022"}
            st.link_button("📄 Dossier Complet INSEE", f"https://www.insee.fr/fr/statistiques/2011101?geo={prefixes.get(area_key, 'COM')}-{final_code}", use_container_width=True, type="primary")
            
            gdf = get_geometry_robust(area_key, final_code, final_name)
            if gdf is not None:
                st.success("✅ Contour chargé")
                st.download_button("📥 GeoJSON", gdf.to_json(), f"{final_code}.geojson")
            else: st.error("❌ Contour non trouvé")

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
