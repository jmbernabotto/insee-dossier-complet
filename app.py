import streamlit as st
import pandas as pd
import requests
import unidecode
import folium
from streamlit_folium import folium_static
import geopandas as gpd

# Configuration
st.set_page_config(page_title="Public-IA : Géo Finder", page_icon="🗺️", layout="wide")

# Récupération de la clé API
API_KEY = st.secrets.get("INSEE_API_KEY", "dfc20306-246c-477c-8203-06246c977cba")

st.title("🗺️ Recherche & Cartographie INSEE")

@st.cache_data(show_spinner="Chargement des référentiels...")
def load_data(area_type):
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    try:
        # On utilise l'API de métadonnées directe (très léger en mémoire)
        endpoints = {
            "EPCI": "intercommunalites", 
            "communes": "communes",
            "departements": "departements", 
            "regions": "regions",
            "airesDAttractionDesVilles2020": "airesDAttractionDesVilles2020",
            "unitesUrbaines2020": "unitesUrbaines2020",
            "zonesDEmploi2020": "zonesDEmploi2020"
        }
        url = f"https://api.insee.fr/metadonnees/geo/{endpoints.get(area_type, area_type)}"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            df = pd.DataFrame(resp.json())
            title_col = 'intituleComplet' if 'intituleComplet' in df.columns else 'intitule'
            df = df.rename(columns={'code': 'CODE', title_col: 'TITLE'})
            df['DISPLAY_TITLE'] = df['TITLE'] + " (" + df['CODE'] + ")"
            return df[['CODE', 'TITLE', 'DISPLAY_TITLE']]
    except: pass
    return pd.DataFrame()

@st.cache_data(show_spinner="Recherche de l'EPCI...")
def get_epci_from_commune(com_code):
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
    url = f"https://api.insee.fr/metadonnees/geo/commune/{com_code}/ascendants?type=Intercommunalite"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200 and r.json():
            return {'CODE': r.json()[0]['code'], 'TITLE': r.json()[0]['intitule']}
    except: pass
    return None

@st.cache_data(show_spinner="Génération de la carte...")
def get_geometry_osm(name, area_type):
    """Source principale légère via Nominatim pour éviter les crashs mémoire"""
    try:
        clean_name = name.split('(')[0].strip()
        query = f"{clean_name}, France"
        if area_type == "EPCI": query = f"Intercommunalité {clean_name}, France"
        
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=geojson&polygon_geojson=1&limit=1"
        resp = requests.get(url, headers={'User-Agent': 'PublicIA-GeoApp'}, timeout=10)
        if resp.status_code == 200 and resp.json()['features']:
            gdf = gpd.GeoDataFrame.from_features(resp.json()['features'])
            gdf.crs = "EPSG:4326"
            return gdf
    except: pass
    return None

# --- Interface ---
categories = {
    "Administratif": {"Communes": "communes", "Intercommunalités (EPCI)": "EPCI", "Départements": "departements", "Régions": "regions"},
    "Zonages d'étude": {"Aires d'attraction (2020)": "airesDAttractionDesVilles2020", "Unités Urbaines (2020)": "unitesUrbaines2020", "Zones d'emploi (2020)": "zonesDEmploi2020"}
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
                results = df_list[mask].head(50)
                if not results.empty:
                    sel = st.sidebar.selectbox("EPCI", results['DISPLAY_TITLE'].tolist())
                    row = results[results['DISPLAY_TITLE'] == sel].iloc[0]
                    final_code, final_name = row['CODE'], row['TITLE']
            else:
                df_com = load_data("communes")
                mask_com = df_com['TITLE'].str.contains(search_query, case=False, na=False) | df_com['CODE'].str.contains(search_query)
                com_results = df_com[mask_com].head(50)
                if not com_results.empty:
                    sel_com = st.sidebar.selectbox("Commune", com_results['DISPLAY_TITLE'].tolist())
                    com_code = com_results[com_results['DISPLAY_TITLE'] == sel_com].iloc[0]['CODE']
                    epci = get_epci_from_commune(com_code)
                    if epci: final_code, final_name = epci['CODE'], epci['TITLE']
    else:
        search_query = st.sidebar.text_input("🔍 Rechercher", placeholder="Nom ou Code...")
        if search_query:
            mask = df_list.apply(lambda row: all(kw in unidecode.unidecode(row['TITLE']).lower() for kw in unidecode.unidecode(search_query).lower().split()) or search_query in str(row['CODE']), axis=1)
            results = df_list[mask].head(50)
            if not results.empty:
                sel = st.sidebar.selectbox("Résultat", results['DISPLAY_TITLE'].tolist())
                row = results[results['DISPLAY_TITLE'] == sel].iloc[0]
                final_code, final_name = row['CODE'], row['TITLE']

    if final_code:
        col_info, col_map = st.columns([1, 3])
        with col_info:
            st.subheader(final_name)
            st.metric("Code", final_code)
            prefix_map = {"communes": "COM", "EPCI": "EPCI", "departements": "DEP", "regions": "REG", "airesDAttractionDesVilles2020": "AAV2020", "unitesUrbaines2020": "UU2020", "zonesDEmploi2020": "ZE2020", "bassinsDeVie2022": "BV2022"}
            url = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix_map.get(area_key, 'COM')}-{final_code}"
            st.link_button("📄 Dossier INSEE", url, use_container_width=True, type="primary")
            
            gdf = get_geometry_osm(final_name, area_key)
            if gdf is not None:
                st.success("✅ Limites trouvées")
                st.download_button("📥 GeoJSON", gdf.to_json(), f"{final_code}.geojson")
            else: st.warning("⚠️ Contour indisponible")

        with col_map:
            if gdf is not None:
                centroid = gdf.geometry.centroid.iloc[0]
                m = folium.Map(location=[centroid.y, centroid.x], zoom_start=11, tiles=None)
                folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Satellite').add_to(m)
                folium.TileLayer('OpenStreetMap', name="Plan").add_to(m)
                folium.GeoJson(gdf, name="Limite", style_function=lambda x: {'fillColor': '#318ce7', 'color': 'black', 'weight': 2, 'fillOpacity': 0.3}).add_to(m)
                folium.LayerControl(collapsed=False).add_to(m)
                folium_static(m, width=1000, height=600)
            else: st.info("Sélectionnez un territoire.")
else:
    st.error("Données indisponibles.")
