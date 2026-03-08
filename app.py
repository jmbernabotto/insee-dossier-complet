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
        clean_name = unidecode(name).lower().replace(' ', '-').replace('\'', '-')
        url = f"https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/departements/{clean_code}-{clean_name}/departement-{clean_code}-{clean_name}.geojson"
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
    """Récupère toutes les communes d'un territoire parent avec simplification des contours."""
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
                # Simplification des contours pour la performance (0.001 deg ~ 100m)
                gdf['geometry'] = gdf['geometry'].simplify(0.001, preserve_topology=True)
                # Calcul de la densité
                gdf['area_km2'] = gdf.to_crs(epsg=3857).area / 10**6
                gdf['densite'] = gdf['population'] / gdf['area_km2']
                return gdf
    except Exception as e:
        st.error(f"Erreur lors de la récupération des communes : {e}")
    return None

@st.cache_data
def get_pynsee_indicators(commune_codes, indicator_type):
    """Récupère des indicateurs pynsee pour une liste de communes avec mapping robuste."""
    try:
        ds_filo = 'GEO2021FILO2018'
        ds_rp = 'GEO2021RP2018'
        
        # --- FILOSOFI (Revenus / Pauvreté) ---
        if indicator_type == "Niveau de vie des individus (€)":
            df = pynsee.get_local_data(dataset_version=ds_filo, nivgeo='COM', geocodes=commune_codes, variables='INDICS_FILO_DISP')
            return df[df['UNIT'] == 'MEDIANE'] if df is not None else None
        elif indicator_type == "Nombre d'individus au sens fiscal":
            df = pynsee.get_local_data(dataset_version=ds_filo, nivgeo='COM', geocodes=commune_codes, variables='INDICS_FILO_DISP')
            return df[df['UNIT'] == 'NBPERS'] if df is not None else None
        elif indicator_type == "Part des ménages pauvres (%)":
            df = pynsee.get_local_data(dataset_version=ds_filo, nivgeo='COM', geocodes=commune_codes, variables='INDICS_FILO_DISP_DET')
            return df[df['UNIT'] == 'TP60'] if df is not None else None
        elif indicator_type == "Part des logements sociaux (%)":
            df = pynsee.get_local_data(dataset_version=ds_filo, nivgeo='COM', geocodes=commune_codes, variables='INDICS_FILO_DISP_DET-OCCTYPR')
            # Variable indicative
            return df if df is not None else None

        # --- RECENSEMENT (RP) ---
        # Population Municipale (Source POPLEG)
        if indicator_type.startswith("Population municipale"):
            df = pynsee.get_local_data(dataset_version='POPLEG2018', nivgeo='COM', geocodes=commune_codes, variables='IND_POPLEGALES')
            if df is not None and not df.empty:
                if "(homme)" in indicator_type or "(femme)" in indicator_type:
                    # Proxy via RP 2011 (Sexe disponible)
                    df_sex = pynsee.get_local_data(dataset_version='GEO2019RP2011', nivgeo='COM', geocodes=commune_codes, variables='SEXE-AGE15_15_90')
                    if df_sex is not None:
                        sex_code = '1' if '(homme)' in indicator_type else '2'
                        df_res = df_sex.groupby(['CODEGEO', 'SEXE'])['OBS_VALUE'].sum().reset_index()
                        return df_res[df_res['SEXE'] == sex_code].rename(columns={'OBS_VALUE': 'OBS_VALUE_SEX'})
                return df[df['UNIT'] == 'POPMUN']

        # Indicateurs Thématiques (RP 2018)
        mapping_rp = {
            "Part des résidences principales (%)": ("STOCD", "10"),
            "Part des appartements parmi les résidences principales (%)": ("TYPLR-CATL", "2"),
            "Part des couples avec enfants (%)": ("TF4", "2"),
            "Part des familles monoparentales (%)": ("TF4", "4"),
            "Part de la population étrangère (%)": ("NAT1", "2"),
            "Part des hommes actifs de 15 à 64 ans (%)": ("TACTR", "11"),
            "Part des femmes actives de 15 à 64 ans (%)": ("TACTR", "11"),
            "Part des actifs occupés de 15 ans ou plus utilisant la marche ou le vélo (%)": ("TRANS_19", "1"),
            "Part des actifs occupés de 15 ans ou plus utilisant les transports en commun (%)": ("TRANS_19", "2"),
            "Surface moyenne des logements (m²)": ("SURF_15-CS1_8-TYPLR", "ENS"),
            "Part des ménages propriétaires (%)": ("STOCD", "10"),
            "Part des ménages d'une seule personne (%)": ("TYPMR", "1"),
            "Part des ménages de 5 personnes ou plus (%)": ("NPERC-NBPIR-TYPLR", "5"),
            "Part de la population âgée de moins de 15 ans (%)": ("AGEFOR5-TF4", "00"),
            "Part de la population âgée de 65 ans ou plus (%)": ("AGEMEN8_A", "65"),
            "Part de la population née en France (%)": ("NAT1", "1"),
        }

        if indicator_type in mapping_rp:
            var, code = mapping_rp[indicator_type]
            df = pynsee.get_local_data(dataset_version=ds_rp, nivgeo='COM', geocodes=commune_codes, variables=var)
            if df is not None and not df.empty:
                # Filtrage spécifique pour la surface (on prend la moyenne ENS)
                if indicator_type == "Surface moyenne des logements (m²)":
                    return df[(df['SURF_15'] == 'ENS') & (df['CS1_8'] == 'ENS') & (df['TYPLR'] == 'ENS')]
                
                # Filtrage par sexe pour les actifs si nécessaire
                if "femmes actives" in indicator_type.lower() and "SEXE" in df.columns:
                    df = df[df['SEXE'] == '2']
                elif "hommes actifs" in indicator_type.lower() and "SEXE" in df.columns:
                    df = df[df['SEXE'] == '1']

                # Filtrage standard
                if var in df.columns:
                    return df[df[var] == code]
                
                # Fallback multi-colonne (variables composées)
                for col in df.columns:
                    if col.startswith(var.split('-')[0]):
                        return df[df[col] == code]
                return df

        # Calculs spécifiques
        if indicator_type == "Indice de jeunesse":
            df = pynsee.get_local_data(dataset_version='GEO2019RP2011', nivgeo='COM', geocodes=commune_codes, variables='SEXE-AGE15_15_90')
            if df is not None:
                # AGE15_15_90 : tranches de 15 ans
                df['is_young'] = df['AGE15_15_90'].isin(['00', '15'])
                df['is_old'] = df['AGE15_15_90'].isin(['60', '75', '90'])
                res = df.groupby('CODEGEO').apply(
                    lambda x: x[x['is_young']]['OBS_VALUE'].sum() / x[x['is_old']]['OBS_VALUE'].sum() if x[x['is_old']]['OBS_VALUE'].sum() > 0 else 0
                ).reset_index()
                res.columns = ['CODEGEO', 'OBS_VALUE']
                return res

    except Exception as e:
        print(f"DEBUG: Erreur Pynsee pour {indicator_type}: {e}")
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

    # Données géographiques de base via geo.api.gouv.fr
    geo_mapping = {
        "communes": "communes",
        "EPCI": "epcis",
        "intercommunalites": "epcis",
        "departements": "departements",
        "regions": "regions"
    }
    
    api_kind = geo_mapping.get(kind)
    if api_kind:
        try:
            fields = "population,surface"
            if kind == "communes":
                fields += ",codesPostaux,codeDepartement,codeRegion"
            
            r = requests.get(f"https://geo.api.gouv.fr/{api_kind}/{code}?fields={fields}")
            if r.status_code == 200:
                data = r.json()
                if 'population' in data:
                    indicators['Population'] = data.get('population')
                if 'surface' in data:
                    indicators['Surface (ha)'] = data.get('surface')
                    if indicators.get('Population') and indicators['Surface (ha)'] > 0:
                        # Densité : Pop / (Surface en ha / 100) = hab/km2
                        indicators['Densité (hab/km²)'] = round(indicators['Population'] / (indicators['Surface (ha)'] / 100), 1)
                if 'codeDepartement' in data:
                    indicators['Code Département'] = data.get('codeDepartement')
        except:
            if code == "62498" and kind == "communes": # Fallback Lens
                indicators['Population'] = 32920
                indicators['Surface (ha)'] = 1170
                indicators['Densité (hab/km²)'] = 2813.7

    # Récupération de données complémentaires via Pynsee (Recensement)
    try:
        nivgeo = "COM" if kind == "communes" else ("EPCI" if kind in ["EPCI", "intercommunalites"] else ("DEP" if kind == "departements" else "REG"))
        # Indicateurs Logement (RP)
        df_rp = pynsee.get_local_data(dataset_version='GEO2021RP2018', nivgeo=nivgeo, geocodes=[code], variables='STOCD-CATL')
        if df_rp is not None and not df_rp.empty:
            # Taux de propriétaires (STOCD 10)
            prop = df_rp[df_rp['STOCD'] == '10']
            total_log = df_rp[df_rp['STOCD'] == 'ENS']
            if not prop.empty and not total_log.empty:
                indicators['Part des propriétaires (%)'] = round((prop.iloc[0]['OBS_VALUE'] / total_log.iloc[0]['OBS_VALUE']) * 100, 1)
            
            # Taux de résidences secondaires (CATL 2)
            sec = df_rp[df_rp['CATL'] == '2']
            if not sec.empty and not total_log.empty:
                indicators['Part des résidences secondaires (%)'] = round((sec.iloc[0]['OBS_VALUE'] / total_log.iloc[0]['OBS_VALUE']) * 100, 1)
    except Exception as e:
        print(f"Erreur RP Pynsee pour {code}: {e}")
    
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
                indicators = get_territory_indicators(row['CODE'], type_col)
                
                # En-tête avec boutons d'accès rapide
                col_title, col_btns = st.columns([2, 1])
                with col_title:
                    st.write(f"**Type :** {label_type} | **Code :** {row['CODE']}")
                with col_btns:
                    prefix = "EPCI" if type_col in ["EPCI", "intercommunalites"] else ("COM" if type_col == "communes" else ("DEP" if type_col == "departements" else "REG"))
                    url_insee = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{row['CODE']}"
                    st.link_button("📄 Dossier INSEE", url_insee, use_container_width=True)

                st.divider()

                # Trois colonnes d'indicateurs fondamentaux
                m1, m2, m3 = st.columns(3)
                
                with m1:
                    st.subheader("📊 Démographie")
                    if 'Population' in indicators:
                        st.metric("Population totale", f"{int(indicators['Population']):,} hab.".replace(',', ' '))
                    if 'Densité (hab/km²)' in indicators:
                        st.metric("Densité", f"{indicators['Densité (hab/km²)']} hab/km²")
                    if 'Surface (ha)' in indicators:
                        st.write(f"Surface : {int(indicators['Surface (ha)']):,} ha".replace(',', ' '))

                with m2:
                    st.subheader("💰 Social & Revenus")
                    if 'Niveau de vie Médian (€)' in indicators:
                        st.metric("Niveau de vie (médian)", f"{int(indicators['Niveau de vie Médian (€)']):,} €".replace(',', ' '))
                    if 'Taux de pauvreté (%)' in indicators:
                        st.metric("Taux de pauvreté", f"{indicators['Taux de pauvreté (%)']}%")
                    if "Part des revenus d'activité (%)" in indicators:
                        val_activite = indicators["Part des revenus d'activité (%)"]
                        st.caption(f"Revenus d'activité : {val_activite}%")

                with m3:
                    st.subheader("🏠 Logement")
                    if 'Part des propriétaires (%)' in indicators:
                        st.metric("Propriétaires", f"{indicators['Part des propriétaires (%)']}%")
                    if 'Part des résidences secondaires (%)' in indicators:
                        st.metric("Rés. secondaires", f"{indicators['Part des résidences secondaires (%)']}%")

                st.divider()
                
                # Carte de situation et Assistant
                c1, c2 = st.columns([1, 1])
                
                with c1:
                    st.subheader("📍 Localisation")
                    gdf_main = get_geo(row['CODE'], type_col, row['TITLE'])
                    if gdf_main is not None:
                        center = gdf_main.to_crs(epsg=3857).centroid.to_crs(epsg=4326).iloc[0]
                        m = folium.Map(location=[center.y, center.x], zoom_start=7 if type_col in ["regions", "departements"] else 9)
                        for col in gdf_main.columns:
                            if col != 'geometry': gdf_main[col] = gdf_main[col].astype(str)
                        geojson_data = json.loads(gdf_main.to_json())
                        folium.GeoJson(geojson_data).add_to(m)
                        st_folium(m, width=500, height=350, returned_objects=[], key="map_main")
                
                with c2:
                    st.subheader(f"💬 Assistant IA")
                    st.link_button("🗺️ Carte Carroyée (Insee)", "https://www.insee.fr/fr/outil-interactif/7737357/map.html", use_container_width=True)
                    
                    if "messages" not in st.session_state:
                        st.session_state.messages = []

                    # Zone de chat simplifiée pour la vue générale
                    chat_container = st.container(height=300)
                    with chat_container:
                        for message in st.session_state.messages:
                            with st.chat_message(message["role"]):
                                st.markdown(message["content"])

                    if prompt := st.chat_input(f"Question sur {row['TITLE']}"):
                        st.session_state.messages.append({"role": "user", "content": prompt})
                        with chat_container:
                            with st.chat_message("user"):
                                st.markdown(prompt)
                            with st.chat_message("assistant"):
                                with st.spinner("Réflexion..."):
                                    response = ask_gemini(prompt, indicators, row['TITLE'])
                                    st.markdown(response)
                        st.session_state.messages.append({"role": "assistant", "content": response})

            with tab2:
                if type_col in ["communes"]:
                    st.info("Sélectionnez un EPCI ou un Département pour voir la carte communale détaillée.")
                else:
                    st.subheader(f"Carte des communes de : {row['TITLE']}")
                    
                    # Configuration des indicateurs hiérarchisés
                    INDICATORS_CONFIG = {
                        "Recensement de la population 2022 (Iris)": [
                            "Densité de population (hab/km²)",
                            "Indice de jeunesse",
                            "Part de la population étrangère (%)",
                            "Part des résidences principales (%)",
                            "Part des appartements parmi les résidences principales (%)",
                            "Part des ménages ayant emménagé depuis moins de 2 ans (%)",
                            "Part des 15 ans ou plus non scolarisés étant diplômés du supérieur (%)",
                            "Part des 15 ans ou plus non scolarisés sans diplôme ou avec au plus le CEP (%)",
                            "Part des familles monoparentales (%)",
                            "Part des couples avec enfants (%)",
                            "Part des actifs occupés de 15 ans ou plus utilisant la marche ou le vélo (%)",
                            "Part des actifs occupés de 15 ans ou plus utilisant les transports en commun (%)",
                            "Part des hommes actifs de 15 à 64 ans (%)",
                            "Part des femmes actives de 15 à 64 ans (%)",
                            "Part des hommes salariés de 15 ans ou plus à temps partiel (%)",
                            "Part des femmes salariées de 15 ans ou plus à temps partiel (%)"
                        ],
                        "Filosofi 2021 (carreau 200m et 1km)": [
                            "Niveau de vie des individus (€)",
                            "Nombre d'individus au sens fiscal",
                            "Part des familles monoparentales (%) (Filo)",
                            "Part des logements sociaux (%)",
                            "Part des ménages pauvres (%)",
                            "Part des ménages propriétaires (%)",
                            "Part des ménages d'une seule personne (%)",
                            "Part des ménages de 5 personnes ou plus (%)",
                            "Part des personnes âgées de moins de 18 ans (%)",
                            "Part des personnes âgées de 65 ans ou plus (%)",
                            "Surface moyenne des logements (m²)"
                        ],
                        "Recensement de la population 2021 (carreau 1km)": [
                            "Population municipale",
                            "Population municipale (femme)",
                            "Population municipale (homme)",
                            "Part de la population âgée de moins de 15 ans (%)",
                            "Part de la population âgée de 65 ans ou plus (%)",
                            "Part de la population née en France (%)",
                            "Part de la population née dans un pays de l'UE autre que la France (%)",
                            "Part de la population née dans un pays hors de l'UE (%)",
                            "Part de la population résidant un an auparavant ailleurs en France (%)",
                            "Part de la population résidant un an auparavant à l'extérieur de la France (%)"
                        ]
                    }
                    
                    cat_choice = st.selectbox("Catégorie", list(INDICATORS_CONFIG.keys()))
                    indicator_choice = st.selectbox("Indicateur à afficher", INDICATORS_CONFIG[cat_choice])
                    
                    # On utilise st.status pour un feedback détaillé (Streamlit 1.24+)
                    m_choroplet = None
                    with st.status("Récupération des données en cours...", expanded=True) as status:
                        status.write("⌛ Chargement des contours géographiques...")
                        gdf_communes = get_communes_of_territory(row['CODE'], type_col)
                        
                        if gdf_communes is not None:
                            n_communes = len(gdf_communes)
                            status.write(f"✅ {n_communes} communes trouvées.")
                            
                            map_col = None
                            legend_name = indicator_choice
                            fill_color = "YlOrRd"
                            
                            # Logique de récupération des données
                            if indicator_choice == "Densité de population (hab/km²)":
                                map_col = "densite"
                                legend_name = "Densité"
                            elif indicator_choice == "Population municipale":
                                map_col = "population"
                            else:
                                status.write(f"⌛ Interrogation de l'API Insee pour '{indicator_choice}'...")
                                pynsee_df = get_pynsee_indicators(gdf_communes['code'].tolist(), indicator_choice)
                                
                                if pynsee_df is not None and not pynsee_df.empty:
                                    status.write("✅ Données statistiques reçues.")
                                    # Correction : OBS_VALUE_SEX si c'est un indicateur par sexe
                                    v_col = 'OBS_VALUE_SEX' if 'OBS_VALUE_SEX' in pynsee_df.columns else 'OBS_VALUE'
                                    pynsee_df = pynsee_df.rename(columns={v_col: 'val_pynsee', 'CODEGEO': 'code'})
                                    pynsee_df = pynsee_df.drop_duplicates(subset=['code'])
                                    gdf_communes = gdf_communes.merge(pynsee_df[['code', 'val_pynsee']], on='code', how='left')
                                    map_col = "val_pynsee"
                                    
                                    if "Niveau de vie" in indicator_choice: fill_color = "YlGn"
                                    elif "pauvres" in indicator_choice: fill_color = "RdPu"
                                else:
                                    st.warning(f"Indicateur '{indicator_choice}' non disponible ou API Insee saturée.")
                            
                            if map_col:
                                status.write("⌛ Génération de la carte interactive...")
                                # Suppression des lignes avec NaN
                                gdf_plot = gdf_communes.dropna(subset=[map_col])
                                
                                if not gdf_plot.empty:
                                    # Optimisation du centrage : on prend les limites globales
                                    bounds = gdf_plot.total_bounds
                                    center_lat = (bounds[1] + bounds[3]) / 2
                                    center_lon = (bounds[0] + bounds[2]) / 2
                                    
                                    m_choroplet = folium.Map(location=[center_lat, center_lon], zoom_start=9)
                                    
                                    # Export JSON une seule fois
                                    geojson_data = gdf_plot.to_json()
                                    
                                    folium.Choropleth(
                                        geo_data=geojson_data,
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
                                        geojson_data,
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
                                    status.update(label="✅ Analyse cartographique prête !", state="complete")
                                else:
                                    status.update(label="⚠️ Aucune donnée statistique exploitable.", state="error")
                            else:
                                status.update(label="⚠️ Échec de la récupération des données.", state="error")
                        else:
                            status.update(label="❌ Impossible de charger les communes.", state="error")
                    
                    if m_choroplet:
                        st_folium(m_choroplet, width=1000, height=600, key="map_choropleth")

        else:
            st.sidebar.warning("Aucun résultat.")
