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
import pynsee

load_dotenv()

st.set_page_config(page_title="Dossier INSEE", layout="wide")

# Configuration Pynsee
os.environ['insee_key'] = 'dKfEzOwfXe8_Az8K5ZA_pY4MfpYa'
os.environ['insee_secret'] = '4fuwyvonN8U4N9XhyfIc3VRqybga'

# Configuration Gemini
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    # Utilisation du modèle Gemini 3 Flash
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
    if kind in ["communes", "EPCI", "intercommunalites"]:
        m = {"EPCI": "epcis", "intercommunalites": "epcis", "communes": "communes"}
        url = f"https://geo.api.gouv.fr/{m[kind]}/{clean_code}?format=geojson&geometry=contour"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                features = [data] if data.get('type') == 'Feature' else data.get('features', [])
                if features:
                    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        except: pass
        
        # Fallback pour Lens (62498) si l'API échoue
        if clean_code == "62498" and kind == "communes":
            lens_fallback = {
                "type": "Feature",
                "properties": {"nom": "Lens", "code": "62498"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[2.84097, 50.44823], [2.84153, 50.44786], [2.84305, 50.44854], [2.84097, 50.44823]]] # Simplified
                }
            }
            return gpd.GeoDataFrame.from_features([lens_fallback], crs="EPSG:4326")
    
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

@st.cache_data
def get_communes_of_territory(parent_code, parent_kind):
    """Récupère toutes les communes d'un territoire parent (EPCI ou Département)."""
    if parent_kind == "departements":
        url = f"https://geo.api.gouv.fr/departements/{parent_code}/communes?format=geojson&geometry=contour&fields=nom,code,population"
    elif parent_kind in ["EPCI", "intercommunalites"]:
        url = f"https://geo.api.gouv.fr/epcis/{parent_code}/communes?format=geojson&geometry=contour&fields=nom,code,population"
    else:
        return None
    
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get('features'):
                gdf = gpd.GeoDataFrame.from_features(data['features'], crs="EPSG:4326")
                # Calcul de la densité si possible
                gdf['area_km2'] = gdf.to_crs(epsg=3857).area / 10**6
                gdf['densite'] = gdf['population'] / gdf['area_km2']
                return gdf
    except Exception as e:
        st.error(f"Erreur lors de la récupération des communes : {e}")
    return None

@st.cache_data
def get_pynsee_indicators(commune_codes, indicator_type):
    """Récupère des indicateurs pynsee pour une liste de communes."""
    try:
        if indicator_type == "Niveau de vie Médian (€)":
            df = pynsee.get_local_data(dataset_version='GEO2021FILO2018', 
                                      nivgeo='COM', 
                                      geocodes=commune_codes,
                                      variables='INDICS_FILO_DISP')
            if df is not None and 'UNIT' in df.columns:
                df = df[df['UNIT'] == 'MEDIANE']
            return df
        elif indicator_type == "Taux de pauvreté (%)":
            df = pynsee.get_local_data(dataset_version='GEO2021FILO2018', 
                                      nivgeo='COM', 
                                      geocodes=commune_codes,
                                      variables='INDICS_FILO_DISP_DET')
            if df is not None and 'UNIT' in df.columns:
                df = df[df['UNIT'] == 'TP60']
            return df
        elif indicator_type == "Logement (Rés. Secondaires %)":
            df = pynsee.get_local_data(dataset_version='GEO2023RP2020', 
                                      nivgeo='COM', 
                                      geocodes=commune_codes,
                                      variables='LOGEMENT')
            if df is not None and 'LOGEMENT' in df.columns:
                df = df[df['LOGEMENT'] == 'RSECO20']
            return df
    except Exception as e:
        st.warning(f"Erreur Pynsee : {e}")
    return None

@st.cache_data
def get_filosofi_data(code, kind):
    """Récupère les données socio-économiques complètes pour le territoire."""
    # Mapping des niveaux
    level_map = {
        "communes": "COM",
        "EPCI": "EPCI",
        "intercommunalites": "EPCI",
        "departements": "DEP",
        "regions": "REG"
    }
    
    nivgeo = level_map.get(kind)
    if not nivgeo:
        return {}
        
    stats = {}
    try:
        # 1. Données détaillées (Pauvreté, Inégalités)
        df_det = pynsee.get_local_data(dataset_version='GEO2021FILO2018', 
                                  nivgeo=nivgeo, 
                                  geocodes=[code],
                                  variables='INDICS_FILO_DISP_DET')
        
        if df_det is not None and not df_det.empty:
            # Extraction Taux de pauvreté
            tp60 = df_det[df_det['UNIT'] == 'TP60']
            if not tp60.empty:
                stats['Taux de pauvreté (%)'] = tp60.iloc[0]['OBS_VALUE']
            
            # Extraction Rapport Interdécile
            rd = df_det[df_det['UNIT'] == 'RD']
            if not rd.empty:
                stats['Rapport Interdécile (D9/D1)'] = rd.iloc[0]['OBS_VALUE']
                
            # Extraction Part des revenus d'activité
            pact = df_det[df_det['UNIT'] == 'PACT']
            if not pact.empty:
                stats["Part des revenus d'activité (%)"] = pact.iloc[0]['OBS_VALUE']
        
        # 2. Données globales (Médiane)
        df_glob = pynsee.get_local_data(dataset_version='GEO2021FILO2018', 
                                  nivgeo=nivgeo, 
                                  geocodes=[code],
                                  variables='INDICS_FILO_DISP')
        
        if df_glob is not None and not df_glob.empty:
            med = df_glob[df_glob['UNIT'] == 'MEDIANE']
            if not med.empty:
                stats['Niveau de vie Médian (€)'] = med.iloc[0]['OBS_VALUE']
                
    except Exception as e:
        print(f"Erreur FILOSOFI pour {code}: {e}")
        
    return stats

def get_territory_indicators(code, kind):
    """Récupère des indicateurs clés pour le territoire sélectionné."""
    indicators = {}
    
    # Prefix for INSEE URL
    prefix = "EPCI" if kind in ["EPCI", "intercommunalites"] else ("COM" if kind == "communes" else ("DEP" if kind == "departements" else "REG"))
    indicators['URL Dossier INSEE'] = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{code}"

    # Données géographiques de base
    if kind == "communes":
        try:
            r = requests.get(f"https://geo.api.gouv.fr/communes/{code}?fields=population,surface,codesPostaux,codeDepartement,codeRegion")
            if r.status_code == 200:
                data = r.json()
                indicators['Population'] = data.get('population')
                indicators['Surface (ha)'] = data.get('surface')
                indicators['Code Département'] = data.get('codeDepartement')
        except:
            if code == "62498": # Fallback Lens
                indicators['Population'] = 32920
    
    # Intégration des données FILOSOFI riches (Pauvreté, Revenus)
    # Fonctionne pour Communes, EPCI, Départements
    filo_stats = get_filosofi_data(code, kind)
    indicators.update(filo_stats)
    
    return indicators

def ask_gemini(prompt, context_data, territory_name):
    """Interroge Gemini avec le contexte du territoire."""
    if not GEMINI_KEY:
        return "Erreur : Clé API Gemini non configurée."
    
    context_str = "\n".join([f"- {k}: {v}" for k, v in context_data.items()])
    full_prompt = f"""Tu es un expert en démographie et géographie française, spécialisé dans l'analyse des données INSEE.
Tu assistes un utilisateur qui consulte le dossier de la collectivité : {territory_name}.

Voici les données clés (issues des bases officielles INSEE / FILOSOFI) pour ce territoire :
{context_str}

L'utilisateur demande : {prompt}

Instructions :
1. Utilise les données chiffrées fournies ci-dessus (Pauvreté, Niveau de vie, Population) en priorité absolue.
2. Si la question porte sur une précision géographique très fine (quartier, rue, carreaux de 200m), mentionne que l'utilisateur peut consulter la "Carte interactive (Carroyage 200m)" via le bouton dédié pour visualiser les données à l'échelle infra-communale.
3. Si tu n'as pas de réponse à la question (que ce soit via les données fournies ou tes connaissances générales), réponds exactement : "je ne peux répondre à votre question".
4. Analyse les indicateurs de pauvreté et de niveau de vie pour donner un contexte social précis.
5. Donne des réponses précises, analytiques et polies.
"""

    try:
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        return f"Erreur lors de la génération : {e}"

st.title("📊 Dossier INSEE")

# Mapping pour l'API INSEE (Uniquement les points d'entrée validés 200 OK)
type_mapping = {
    "Communes": "communes",
    "EPCI (Intercommunalités)": "intercommunalites",
    "Départements": "departements",
    "Régions": "regions",
    "Arrondissements": "arrondissements",
    "Arrondissements Municipaux (Paris, Lyon, Marseille)": "arrondissementsMunicipaux",
    "Communes Associées / Déléguées": "communesDeleguees"
}

label_type = st.sidebar.selectbox("Type", list(type_mapping.keys()))
type_col = type_mapping[label_type]
data = load_insee(type_col)

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
    if type_col in ["EPCI", "intercommunalites"]: df['CODE'] = df['CODE'].str.zfill(9)
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
        res = df[mask].copy()
        
        if not res.empty:
            # Priorisation : Exact match en premier
            res['is_exact'] = (res['SEARCH_KEY'] == search_norm) | (res['CODE'] == search)
            res = res.sort_values(by='is_exact', ascending=False).head(10)
            
            sel = st.sidebar.selectbox("Choisir", res['DISPLAY'].tolist())
            row = res[res['DISPLAY'] == sel].iloc[0]
            
            # Reset conversation if territory changes
            if "current_territory" not in st.session_state or st.session_state.current_territory != row['CODE']:
                st.session_state.current_territory = row['CODE']
                st.session_state.messages = []

            st.header(row['TITLE'])
            
            # --- ONGLET ---
            tab1, tab2 = st.tabs(["📌 Vue Générale", "🗺️ Analyse Cartographique (Communes)"])

            with tab1:
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.metric("Territoire", row['TITLE'])
                    st.write(f"Code : {row['CODE']}")
                    
                    prefix = "EPCI" if type_col in ["EPCI", "intercommunalites"] else ("COM" if type_col == "communes" else ("DEP" if type_col == "departements" else "REG"))
                    url_insee = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{row['CODE']}"
                    
                    st.link_button("📄 Voir le dossier complet", url_insee, use_container_width=True)
                    st.link_button("🗺️ Carte interactive (Carroyage 200m)", "https://www.insee.fr/fr/outil-interactif/7737357/map.html", use_container_width=True)
                    st.caption("💡 Cliquez sur le bouton **Imprimer** en haut du dossier pour le format PDF.")
                
                gdf_main = get_geo(row['CODE'], type_col, row['TITLE'])
                if gdf_main is not None:
                    with col2:
                        center = gdf_main.to_crs(epsg=3857).centroid.to_crs(epsg=4326).iloc[0]
                        m = folium.Map(location=[center.y, center.x], zoom_start=7 if type_col in ["regions", "departements"] else 9)
                        
                        for col in gdf_main.columns:
                            if col != 'geometry':
                                gdf_main[col] = gdf_main[col].astype(str)
                        
                        geojson_data = json.loads(gdf_main.to_json())
                        folium.GeoJson(geojson_data).add_to(m)
                        st_folium(m, width=700, height=400, returned_objects=[], key="map_main")

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

            with tab2:
                if type_col in ["communes"]:
                    st.info("Sélectionnez un EPCI ou un Département pour voir la carte communale détaillée.")
                else:
                    st.subheader(f"Carte des communes de : {row['TITLE']}")
                    
                    indicator_choice = st.selectbox("Indicateur à afficher", 
                                                   ["Population", "Densité (hab/km²)", "Niveau de vie Médian (€)", "Taux de pauvreté (%)", "Logement (Rés. Secondaires %)"])
                    
                    with st.spinner("Chargement des données géographiques et statistiques..."):
                        gdf_communes = get_communes_of_territory(row['CODE'], type_col)
                        
                        if gdf_communes is not None:
                            map_col = "population"
                            legend_name = "Population"
                            fill_color = "YlOrRd"
                            
                            if indicator_choice == "Densité (hab/km²)":
                                map_col = "densite"
                                legend_name = "Densité"
                            elif indicator_choice == "Niveau de vie Médian (€)":
                                pynsee_df = get_pynsee_indicators(gdf_communes['code'].tolist(), "Niveau de vie Médian (€)")
                                if pynsee_df is not None and not pynsee_df.empty:
                                    pynsee_df = pynsee_df.rename(columns={'OBS_VALUE': 'rev_med', 'CODEGEO': 'code'})
                                    gdf_communes = gdf_communes.merge(pynsee_df[['code', 'rev_med']], on='code', how='left')
                                    map_col = "rev_med"
                                    legend_name = "Niveau de vie Médian (€)"
                                    fill_color = "YlGn"
                            elif indicator_choice == "Taux de pauvreté (%)":
                                pynsee_df = get_pynsee_indicators(gdf_communes['code'].tolist(), "Taux de pauvreté (%)")
                                if pynsee_df is not None and not pynsee_df.empty:
                                    pynsee_df = pynsee_df.rename(columns={'OBS_VALUE': 'pauvreté', 'CODEGEO': 'code'})
                                    gdf_communes = gdf_communes.merge(pynsee_df[['code', 'pauvreté']], on='code', how='left')
                                    map_col = "pauvreté"
                                    legend_name = "Taux de pauvreté (%)"
                                    fill_color = "RdPu"
                            elif indicator_choice == "Logement (Rés. Secondaires %)":
                                pynsee_df = get_pynsee_indicators(gdf_communes['code'].tolist(), "Logement (Rés. Secondaires %)")
                                if pynsee_df is not None and not pynsee_df.empty:
                                    pynsee_df = pynsee_df.rename(columns={'OBS_VALUE': 'res_sec', 'CODEGEO': 'code'})
                                    gdf_communes = gdf_communes.merge(pynsee_df[['code', 'res_sec']], on='code', how='left')
                                    map_col = "res_sec"
                                    legend_name = "Résidences Secondaires"
                            
                            # Suppression des lignes avec NaN pour la carte
                            gdf_plot = gdf_communes.dropna(subset=[map_col])
                            
                            if not gdf_plot.empty:
                                center_c = gdf_plot.to_crs(epsg=3857).centroid.to_crs(epsg=4326).unary_union.centroid
                                m_choroplet = folium.Map(location=[center_c.y, center_c.x], zoom_start=9)
                                
                                folium.Choropleth(
                                    geo_data=gdf_plot.to_json(),
                                    name="choropleth",
                                    data=gdf_plot,
                                    columns=["code", map_col],
                                    key_on="feature.properties.code",
                                    fill_color=fill_color,
                                    fill_opacity=0.7,
                                    line_opacity=0.2,
                                    legend_name=legend_name,
                                ).add_to(m_choroplet)
                                
                                tooltip = folium.features.GeoJson(
                                    gdf_plot.to_json(),
                                    style_function=lambda x: {'fillColor': '#ffffff', 'color':'#000000', 'fillOpacity': 0.1, 'weight': 0.1},
                                    control=False,
                                    highlight_function=lambda x: {'fillColor': '#000000', 'color':'#000000', 'fillOpacity': 0.5, 'weight': 0.1},
                                    tooltip=folium.features.GeoJsonTooltip(
                                        fields=['nom', 'code', map_col],
                                        aliases=['Commune: ', 'Code: ', f'{legend_name}: '],
                                        style=("background-color: white; color: #333333; font-family: arial; font-size: 12px; padding: 10px;")
                                    )
                                )
                                m_choroplet.add_child(tooltip)
                                st_folium(m_choroplet, width=1000, height=600, key="map_choropleth")
                            else:
                                st.warning("Aucune donnée à afficher pour cet indicateur.")
                        else:
                            st.error("Impossible de charger les données communales.")

        else:
            st.sidebar.warning("Aucun résultat.")
