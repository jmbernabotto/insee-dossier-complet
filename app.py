import streamlit as st
import pandas as pd
import requests
import geopandas as gpd
import folium
import json
import numpy as np
from unidecode import unidecode
from streamlit_folium import st_folium
import io
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Dossier INSEE", layout="wide")

# Configuration Gemini
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-3-flash-preview')
else:
    st.sidebar.error("Clé API Gemini manquante dans le fichier .env")

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
    clean_code = str(code).strip()
    
    # Stratégie différenciée selon le type de territoire
    if kind in ["communes", "EPCI"]:
        m = {"EPCI": "epcis", "communes": "communes"}
        url = f"https://geo.api.gouv.fr/{m[kind]}/{clean_code}?format=geojson&geometry=contour"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                features = [data] if data.get('type') == 'Feature' else data.get('features', [])
                if features:
                    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        except: pass
    
    elif kind == "departements":
        # Source alternative fiable pour les départements
        url = f"https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/departements/{clean_code}-{name.lower().replace(' ', '-').replace('\'', '-')}/departement-{clean_code}-{name.lower().replace(' ', '-').replace('\'', '-')}.geojson"
        # Version simplifiée de l'URL si la complexe échoue
        urls = [
            url,
            f"https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/departements.geojson"
        ]
        for u in urls:
            try:
                r = requests.get(u, timeout=10)
                if r.status_code == 200:
                    gdf = gpd.read_file(io.StringIO(r.text))
                    # Si on a chargé le fichier complet, on filtre
                    if 'code' in gdf.columns:
                        gdf = gdf[gdf['code'] == clean_code]
                    if not gdf.empty: return gdf
            except: continue

    elif kind == "regions":
        url = "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/regions.geojson"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                gdf = gpd.read_file(io.StringIO(r.text))
                if 'code' in gdf.columns:
                    gdf = gdf[gdf['code'] == clean_code]
                if not gdf.empty: return gdf
        except: pass
        
    return None

def get_territory_indicators(code, kind):
    """Récupère des indicateurs clés pour le territoire sélectionné."""
    indicators = {}
    
    # Prefix for INSEE URL
    prefix = "EPCI" if kind == "EPCI" else ("COM" if kind == "communes" else ("DEP" if kind == "departements" else "REG"))
    indicators['URL Dossier INSEE'] = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{code}"

    if kind == "communes":
        try:
            r = requests.get(f"https://geo.api.gouv.fr/communes/{code}?fields=population,surface,codesPostaux,codeDepartement,codeRegion")
            if r.status_code == 200:
                data = r.json()
                indicators['Population'] = data.get('population')
                indicators['Surface (ha)'] = data.get('surface')
                indicators['Codes Postaux'] = ", ".join(data.get('codesPostaux', []))
                indicators['Code Département'] = data.get('codeDepartement')
                indicators['Code Région'] = data.get('codeRegion')
        except: pass
    
    # Optionnel: On pourrait ajouter des données pynsee ici si besoin
    # Pour l'instant on se base sur l'URL et les données de base
    return indicators

def ask_gemini(prompt, context_data, territory_name):
    """Interroge Gemini avec le contexte du territoire."""
    if not GEMINI_KEY:
        return "Erreur : Clé API Gemini non configurée."
    
    context_str = "\n".join([f"- {k}: {v}" for k, v in context_data.items()])
    full_prompt = f"""Tu es un expert en démographie et géographie française, spécialisé dans l'analyse des données INSEE.
Tu assistes un utilisateur qui consulte le dossier de la collectivité : {territory_name}.

Voici les données clés dont tu scratches pour ce territoire :
{context_str}

L'utilisateur demande : {prompt}

Instructions :
1. Utilise les données fournies ci-dessus en priorité.
2. Si la question porte sur des détails non présents (ex: taux de chômage, pyramide des âges), mentionne que ces informations sont disponibles dans le "Dossier complet" via l'URL fournie.
3. Donne des réponses précises, analytiques et polies.
4. Si tu as des connaissances générales sur {territory_name} qui complètent les données, n'hésite pas à les partager pour enrichir la réponse.
"""

    try:
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        return f"Erreur lors de la génération : {e}"

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
    
    # Création du libellé d'affichage (Titre + Code)
    df['DISPLAY'] = df['TITLE'] + " (" + df['CODE'] + ")"
    
    # Clé de recherche normalisée (sans accents, sans tirets)
    df['SEARCH_KEY'] = df['TITLE'].apply(lambda x: unidecode(str(x)).lower().replace('-', ' '))
    
    search = st.sidebar.text_input("Rechercher")
    if search:
        # Normalisation de la saisie utilisateur
        search_norm = unidecode(search).lower().replace('-', ' ')
        
        mask = df['SEARCH_KEY'].str.contains(search_norm, na=False) | df['CODE'].str.contains(search, na=False)
        res = df[mask].head(10)
        
        if not res.empty:
            sel = st.sidebar.selectbox("Choisir", res['DISPLAY'].tolist())
            row = res[res['DISPLAY'] == sel].iloc[0]
            
            # Reset conversation if territory changes
            if "current_territory" not in st.session_state or st.session_state.current_territory != row['CODE']:
                st.session_state.current_territory = row['CODE']
                st.session_state.messages = []

            gdf = get_geo(row['CODE'], type_col, row['TITLE'])
            
            st.header(row['TITLE'])
            
            col1, col2 = st.columns([1, 2])
            col1.metric("Territoire", row['TITLE'])
            col1.write(f"Code : {row['CODE']}")
            
            prefix = "EPCI" if type_col == "EPCI" else ("COM" if type_col == "communes" else ("DEP" if type_col == "departements" else "REG"))
            url_insee = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{row['CODE']}"
            
            col1.link_button("📄 Voir le dossier complet", url_insee, use_container_width=True)
            col1.caption("💡 Cliquez sur le bouton puis sur **Imprimer** en haut du dossier et choisir le format PDF.")
            
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
            
            st.divider()
            st.subheader(f"💬 Assistant IA - {row['TITLE']}")
            
            if "messages" not in st.session_state:
                st.session_state.messages = []

            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

            if prompt := st.chat_input(f"Posez une question sur {row['TITLE']}"):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("user"):
                    st.markdown(prompt)

                with st.chat_message("assistant"):
                    with st.spinner("Réflexion..."):
                        indicators = get_territory_indicators(row['CODE'], type_col)
                        response = ask_gemini(prompt, indicators, row['TITLE'])
                        st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
        else:
            st.sidebar.warning("Aucun résultat.")
