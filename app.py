import streamlit as st
import pandas as pd
import requests
import geopandas as gpd
import folium
from streamlit_folium import st_folium
import io

# Configuration de la page
st.set_page_config(page_title="INSEE Géo Finder", layout="wide")

# Clé API INSEE (depuis secrets ou défaut)
INSEE_KEY = st.secrets.get("INSEE_API_KEY", "dfc20306-246c-477c-8203-06246c977cba")

@st.cache_data(show_spinner="Chargement des données INSEE...")
def fetch_insee(endpoint):
    headers = {"Authorization": f"Bearer {INSEE_KEY}", "Accept": "application/json"}
    url = f"https://api.insee.fr/metadonnees/geo/{endpoint}"
    try:
        r = requests.get(url, headers=headers)
        return r.json() if r.status_code == 200 else []
    except: return []

@st.cache_data(show_spinner="Récupération du contour...")
def fetch_geometry(code, area_type, name):
    # 1. Tentative via API Géo Etalab (Officiel et très fiable)
    mapping = {"EPCI": "epcis", "communes": "communes", "departements": "departements", "regions": "regions"}
    if area_type in mapping:
        url = f"https://geo.api.gouv.fr/{mapping[area_type]}/{code}?format=geojson&geometry=contour"
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                gdf = gpd.read_file(io.StringIO(r.text))
                if not gdf.empty:
                    gdf.crs = "EPSG:4326"
                    return gdf
        except: pass

    # 2. Fallback Nominatim (OpenStreetMap)
    q = name.split('(')[0].strip()
    if area_type == "EPCI":
        q = q.replace("CA ", "Communauté d'agglomération ").replace("CC ", "Communauté de communes ")
    
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": f"{q}, France", "format": "geojson", "polygon_geojson": 1, "limit": 1}
        r = requests.get(url, params=params, headers={'User-Agent': 'GeoApp-v5'}, timeout=10)
        if r.status_code == 200 and r.json().get('features'):
            gdf = gpd.GeoDataFrame.from_features(r.json())
            gdf.crs = "EPSG:4326"
            return gdf
    except: pass
    return None

# Barre latérale
st.sidebar.title("📌 Sélection du territoire")
type_options = {
    "Communes": "communes",
    "EPCI (Intercommunalités)": "intercommunalites",
    "Départements": "departements",
    "Régions": "regions"
}
selected_label = st.sidebar.selectbox("Type de collectivité", list(type_options.keys()))
endpoint = type_options[selected_label]
area_type_key = "EPCI" if "EPCI" in selected_label else endpoint

data = fetch_insee(endpoint)
final_code, final_name = None, None

if data:
    df = pd.DataFrame(data)
    title_col = 'intituleComplet' if 'intituleComplet' in df.columns else 'intitule'
    df = df.rename(columns={'code': 'CODE', title_col: 'TITLE'})
    df['CODE'] = df['CODE'].astype(str)
    
    # Padding des codes
    if "Communes" in selected_label: df['CODE'] = df['CODE'].str.zfill(5)
    elif "EPCI" in selected_label: df['CODE'] = df['CODE'].str.zfill(9)
    
    df['DISPLAY'] = df['TITLE'] + " (" + df['CODE'] + ")"
    
    search = st.sidebar.text_input("🔍 Rechercher par nom ou code")
    if search:
        results = df[df['TITLE'].str.contains(search, case=False, na=False) | df['CODE'].str.contains(search)].head(20)
        if not results.empty:
            choice = st.sidebar.selectbox("Résultats trouvés", results['DISPLAY'].tolist())
            row = results[results['DISPLAY'] == choice].iloc[0]
            final_code, final_name = row['CODE'], row['TITLE']

# Affichage principal
st.title("🗺️ Explorateur Géographique INSEE")

if final_code:
    gdf = fetch_geometry(final_code, area_type_key, final_name)
    
    col1, col2 = st.columns([1, 3])
    with col1:
        st.header(final_name)
        st.metric("Code Officiel", final_code)
        
        # Lien vers le dossier INSEE
        prefix = "EPCI" if "EPCI" in selected_label else ("COM" if "Communes" in selected_label else ("DEP" if "Départements" in selected_label else "REG"))
        st.link_button("📄 Voir le dossier INSEE", f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{final_code}", use_container_width=True)
        
        if gdf is not None:
            st.success("✅ Contour géographique chargé")
            st.download_button("📥 Télécharger le GeoJSON", gdf.to_json(), f"{final_code}.geojson", use_container_width=True)
        else:
            st.error("⚠️ Contour indisponible pour ce territoire")
            
    with col2:
        if gdf is not None:
            # Calcul du centre sans warning
            center = gdf.to_crs(epsg=3857).centroid.to_crs(epsg=4326).iloc[0]
            m = folium.Map(location=[center.y, center.x], zoom_start=10)
            folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', attr='Esri', name='Satellite').add_to(m)
            folium.TileLayer('OpenStreetMap', name="Plan").add_to(m)
            folium.GeoJson(gdf, style_function=lambda x: {'fillColor': '#318ce7', 'color': 'black', 'weight': 2, 'fillOpacity': 0.3}).add_to(m)
            folium.LayerControl().add_to(m)
            st_folium(m, width=1000, height=600, returned_objects=[])
        else:
            st.info("Sélectionnez un territoire valide dans la barre latérale pour afficher la carte.")
else:
    st.info("👋 Bienvenue ! Utilisez la barre latérale pour rechercher une collectivité.")
