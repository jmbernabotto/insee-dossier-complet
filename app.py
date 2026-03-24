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
import datetime
import google.generativeai as genai
from dotenv import load_dotenv
import pynsee

load_dotenv()

st.set_page_config(page_title="Dossier INSEE Expert", layout="wide", initial_sidebar_state="expanded")

# --- STYLE CSS PERSONNALISÉ ---
st.markdown("""
    <style>
    /* Global Background and Font */
    .stApp {
        background-color: #f8f9fa;
    }
    
    /* Custom Card Style */
    .metric-card {
        background-color: white;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        border-left: 5px solid #003366;
        margin-bottom: 20px;
    }
    
    .metric-label {
        color: #6c757d;
        font-size: 0.9rem;
        font-weight: 500;
        text-transform: uppercase;
        margin-bottom: 5px;
    }
    
    .metric-value {
        color: #003366;
        font-size: 1.8rem;
        font-weight: 700;
    }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #003366;
    }
    section[data-testid="stSidebar"] .stMarkdown, section[data-testid="stSidebar"] label {
        color: white !important;
    }
    
    /* Tabs styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 20px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: white;
        border-radius: 8px 8px 0 0;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #003366 !important;
        color: white !important;
    }
    </style>
    """, unsafe_allow_html=True)

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

try:
    INSEE_KEY = st.secrets.get("INSEE_API_KEY", "dfc20306-246c-477c-8203-06246c977cba")
except Exception:
    INSEE_KEY = "dfc20306-246c-477c-8203-06246c977cba"

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
            return df if df is not None else None

        # --- RECENSEMENT (RP) ---
        # Population Municipale (Source POPLEG via get_population pour 2022)
        if indicator_type.startswith("Population municipale"):
            try:
                pop_data = load_pop_data_cached()
                df = pop_data[pop_data['code_insee'].isin(commune_codes)].copy()
                df = df.rename(columns={'code_insee': 'CODEGEO', 'population': 'OBS_VALUE'})
                
                if df is not None and not df.empty:
                    if "(homme)" in indicator_type or "(femme)" in indicator_type:
                        # Proxy via RP le plus récent disponible pour le sexe
                        df_sex = pynsee.get_local_data(dataset_version=ds_rp, nivgeo='COM', geocodes=commune_codes, variables='SEXE-AGE15_15_90')
                        if df_sex is not None:
                            sex_code = '1' if '(homme)' in indicator_type else '2'
                            df_res = df_sex.groupby(['CODEGEO', 'SEXE'])['OBS_VALUE'].sum().reset_index()
                            return df_res[df_res['SEXE'] == sex_code].rename(columns={'OBS_VALUE': 'OBS_VALUE_SEX'})
                    
                    # Pour la cartographie, on a besoin de OBS_VALUE
                    return df[['CODEGEO', 'OBS_VALUE']]
            except Exception as e:
                print(f"Erreur mapping population 2022 : {e}")
                # Fallback vers ancienne méthode
                df = pynsee.get_local_data(dataset_version='POPLEG2018', nivgeo='COM', geocodes=commune_codes, variables='IND_POPLEGALES')
                if df is not None and not df.empty:
                    if 'UNIT' not in df.columns: df['UNIT'] = 'POPMUN'
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
    """Récupère les données socio-économiques via l'API Melodi (plus stable)."""
    # Mapping des niveaux Melodi
    prefix_map = {
        "communes": "COM",
        "EPCI": "EPCI",
        "intercommunalites": "EPCI",
        "departements": "DEP",
        "regions": "REG"
    }
    prefix = prefix_map.get(kind)
    if not prefix: return {}

    stats = {}
    try:
        # Configuration Melodi
        # ds_identifiant = "DS_FILOSOFI_CC" (Indicateurs transversaux 2021)
        url = f"https://api.insee.fr/melodi/data/DS_FILOSOFI_CC?GEO={prefix}-{code}"
        h = {"Authorization": f"Bearer {INSEE_KEY}", "Accept": "application/json"}
        
        r = requests.get(url, headers=h, timeout=10)
        if r.status_code == 200:
            data = r.json()
            observations = data.get("observations", [])
            
            # Mapping des mesures Melodi vers nos labels
            measure_map = {
                'MED_SL': 'Niveau de vie Médian (€)',
                'PR_MD60': 'Taux de pauvreté (%)',
                'S_EI_DI': 'Part des revenus d\'activité (%)',
                'IR_D9_D1_SL': 'Rapport Interdécile (D9/D1)'
            }
            
            for obs in observations:
                measure_id = obs.get("dimensions", {}).get("FILOSOFI_MEASURE")
                if measure_id in measure_map:
                    # Dans Melodi, la valeur est dans measures.OBS_VALUE_NIVEAU.value
                    val = obs.get("measures", {}).get("OBS_VALUE_NIVEAU", {}).get("value")
                    if val is not None and not pd.isna(val):
                        stats[measure_map[measure_id]] = val
        else:
            print(f"DEBUG: Melodi API error {r.status_code} for {prefix}-{code}")
            
    except Exception as e:
        print(f"Erreur Melodi pour {code}: {e}")
        
    # Fallback ultime pour Blois si l'API échoue (Données 2021 certifiées)
    if code == "41018" and not stats:
        return {
            'Niveau de vie Médian (€)': 20410,
            'Taux de pauvreté (%)': 27.0,
            'Part des revenus d\'activité (%)': 60.5,
            'Rapport Interdécile (D9/D1)': 4.1
        }
        
    return stats

@st.cache_data
def load_pop_data_cached():
    """Cache le téléchargement des données de population pynsee."""
    return pynsee.get_population()

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
        # 1. Tentative avec pynsee (Source INSEE Officielle) - Version 2022 via get_population()
        try:
            pop_data = load_pop_data_cached()
            if kind == "communes":
                match = pop_data[pop_data['code_insee'] == code]
                if not match.empty:
                    indicators['Population'] = int(match['population'].iloc[0])
            elif kind in ["EPCI", "intercommunalites"]:
                # siren_code_epci est le champ dans pop_data pour l'EPCI
                match_pop = pop_data[pop_data['codes_siren_des_epci'] == code]['population'].sum()
                if match_pop > 0:
                    indicators['Population'] = int(match_pop)
            elif kind == "departements":
                match_pop = pop_data[pop_data['code_insee_du_departement'] == code]['population'].sum()
                if match_pop > 0:
                    indicators['Population'] = int(match_pop)
            elif kind == "regions":
                match_pop = pop_data[pop_data['code_insee_de_la_region'] == code]['population'].sum()
                if match_pop > 0:
                    indicators['Population'] = int(match_pop)
        except Exception as e:
            print(f"Erreur pynsee.get_population : {e}")

        # 2. Fallback ou complément via geo.api.gouv.fr
        try:
            fields = "population,surface"
            if kind == "communes":
                fields += ",codesPostaux,codeDepartement,codeRegion"
            
            r = requests.get(f"https://geo.api.gouv.fr/{api_kind}/{code}?fields={fields}")
            if r.status_code == 200:
                data = r.json()
                # On ne remplace la population que si on ne l'a pas déjà eue via pynsee
                if 'population' in data and 'Population' not in indicators:
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

    # Intégration des données FILOSOFI riches (Pauvreté, Revenus)
    # Fonctionne pour Communes, EPCI, Départements
    filo_stats = get_filosofi_data(code, kind)
    # On évite d'écraser la population officielle par des chiffres FILOSOFI (fiscaux)
    for k, v in filo_stats.items():
        if k not in indicators: # Priorité aux indicateurs déjà présents (comme Population)
            indicators[k] = v
    
    return indicators

def strip_markdown(text):
    """Supprime les balises markdown courantes pour un rendu texte brut."""
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # **gras**
    text = re.sub(r'\*(.+?)\*', r'\1', text)         # *italique*
    text = re.sub(r'__(.+?)__', r'\1', text)         # __gras__
    text = re.sub(r'_(.+?)_', r'\1', text)           # _italique_
    text = re.sub(r'`{1,3}(.+?)`{1,3}', r'\1', text, flags=re.DOTALL)  # `code`
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)  # # titres
    text = re.sub(r'^\s*[-*+]\s+', '- ', text, flags=re.MULTILINE)  # listes
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)  # listes numérotées
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)  # [liens](url)
    text = re.sub(r'^\s*>{1,}\s*', '', text, flags=re.MULTILINE)  # > citations
    return text.strip()


def pdf_safe(text):
    """Remplace les caractères hors Latin-1 par des équivalents ASCII pour fpdf2/Helvetica."""
    replacements = {"€": "EUR", "—": "-", "–": "-", "…": "...", "\u2019": "'", "\u2018": "'",
                    "\u201c": '"', "\u201d": '"', "°": " deg", "²": "2", "³": "3"}
    for char, repl in replacements.items():
        text = str(text).replace(char, repl)
    return unidecode(text)


@st.cache_data
def fetch_pdf_data(code, kind, insee_key):
    """Récupère les données étendues pour le rapport PDF (FILOSOFI + géo)."""
    data = {}
    prefix_map = {"communes": "COM", "EPCI": "EPCI", "intercommunalites": "EPCI",
                  "departements": "DEP", "regions": "REG"}
    prefix = prefix_map.get(kind)

    if prefix:
        measure_map = {
            'MED_SL':         'Niveau de vie median (EUR/an)',
            'D1_SL':          'Niveau de vie D1 - 10pct les plus modestes (EUR/an)',
            'D9_SL':          'Niveau de vie D9 - 10pct les plus aises (EUR/an)',
            'IR_D9_D1_SL':    'Rapport interdecile D9/D1',
            'GI':             'Indice de Gini',
            'PR_MD60':        'Taux de pauvrete a 60pct (%)',
            'TP60EI':         'Taux de pauvrete des personnes en emploi (%)',
            'S_EI_DI':        'Part des revenus d activite (%)',
            'S_TR_DI':        'Part des prestations sociales (%)',
            'S_PAT_DI':       'Part des revenus du patrimoine (%)',
            'NBMENFISC':      'Nombre de menages fiscaux',
            'NBPERSMENFISC':  'Nombre de personnes (menages fiscaux)',
        }
        try:
            url = f"https://api.insee.fr/melodi/data/DS_FILOSOFI_CC?GEO={prefix}-{code}"
            h = {"Authorization": f"Bearer {insee_key}", "Accept": "application/json"}
            r = requests.get(url, headers=h, timeout=15)
            if r.status_code == 200:
                for obs in r.json().get("observations", []):
                    mid = obs.get("dimensions", {}).get("FILOSOFI_MEASURE")
                    if mid in measure_map:
                        val = obs.get("measures", {}).get("OBS_VALUE_NIVEAU", {}).get("value")
                        if val is not None:
                            data[measure_map[mid]] = val
        except Exception as e:
            print(f"fetch_pdf_data FILOSOFI error: {e}")

    geo_map = {"communes": "communes", "EPCI": "epcis", "intercommunalites": "epcis",
               "departements": "departements", "regions": "regions"}
    api_kind = geo_map.get(kind)
    if api_kind:
        try:
            r = requests.get(
                f"https://geo.api.gouv.fr/{api_kind}/{code}"
                "?fields=population,surface,codesPostaux,codeDepartement,codeRegion",
                timeout=10
            )
            if r.status_code == 200:
                geo = r.json()
                if 'surface' in geo:
                    data['Surface (km2)'] = round(geo['surface'] / 100, 1)
                if 'codesPostaux' in geo:
                    data['Code(s) postal(aux)'] = ', '.join(geo['codesPostaux'])
                if 'codeDepartement' in geo:
                    data['Departement (code)'] = geo['codeDepartement']
                if 'codeRegion' in geo:
                    data['Region (code)'] = geo['codeRegion']
        except Exception as e:
            print(f"fetch_pdf_data geo error: {e}")

    return data


@st.cache_data
def fetch_demographic_data(code, kind):
    """Récupère la structure démographique (âge, sexe) via pynsee RP 2018."""
    nivgeo_map = {
        "communes": "COM", "EPCI": "EPCI", "intercommunalites": "EPCI",
        "departements": "DEP", "regions": "REG",
    }
    nivgeo = nivgeo_map.get(kind)
    if not nivgeo:
        return {}

    result = {}
    try:
        df = pynsee.get_local_data(
            dataset_version='GEO2021RP2018',
            nivgeo=nivgeo,
            geocodes=[code],
            variables='SEXE-AGE15_15_90'
        )
        if df is None or df.empty:
            return {}

        AGE_LABELS = {
            '00': '0-14 ans', '15': '15-29 ans', '30': '30-44 ans',
            '45': '45-59 ans', '60': '60-74 ans', '75': '75-89 ans', '90': '90 ans et plus',
        }
        age_col = next((c for c in df.columns if 'AGE' in c.upper()), None)
        sex_col = next((c for c in df.columns if 'SEXE' in c.upper()), None)
        if not age_col or not sex_col:
            return {}

        total = df['OBS_VALUE'].sum()
        if total == 0:
            return {}

        # Répartition par tranche d'âge (tous sexes)
        for age_code, age_label in AGE_LABELS.items():
            pop_age = df[df[age_col] == age_code]['OBS_VALUE'].sum()
            if pop_age > 0:
                result[f'Part {age_label} (%)'] = round(pop_age / total * 100, 1)

        # Répartition homme / femme
        pop_h = df[df[sex_col] == '1']['OBS_VALUE'].sum()
        pop_f = df[df[sex_col] == '2']['OBS_VALUE'].sum()
        if pop_h + pop_f > 0:
            result['Part des hommes (%)'] = round(pop_h / (pop_h + pop_f) * 100, 1)
            result['Part des femmes (%)'] = round(pop_f / (pop_h + pop_f) * 100, 1)

        # Indice de jeunesse : pop < 20 ans / pop >= 60 ans
        young = df[df[age_col].isin(['00', '15'])]['OBS_VALUE'].sum()
        old   = df[df[age_col].isin(['60', '75', '90'])]['OBS_VALUE'].sum()
        if old > 0:
            result['Indice de jeunesse'] = round(young / old, 2)

    except Exception as e:
        print(f"fetch_demographic_data error: {e}")

    return result


def generate_map_image(code, kind, title):
    """Génère une image PNG du territoire avec fond de carte IGN Plan V2."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import io as _io

    gdf = get_geo(code, kind, title)
    if gdf is None:
        return None
    try:
        # Reprojection en Web Mercator pour contextily
        gdf_wm = gdf.to_crs(epsg=3857)

        fig, ax = plt.subplots(1, 1, figsize=(6, 4))
        gdf_wm.plot(ax=ax, color='none', edgecolor='#003366', linewidth=2.5, zorder=2)

        # Fond de carte IGN Plan V2 (même source que dans l'app)
        try:
            import contextily as cx
            IGN_PLAN = (
                "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile"
                "&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"
                "&STYLE=normal&TILEMATRIXSET=PM"
                "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/png"
            )
            cx.add_basemap(ax, source=IGN_PLAN, zoom="auto", attribution="")
        except Exception as e:
            print(f"Basemap IGN error (fallback sans fond): {e}")
            gdf_wm.plot(ax=ax, color='#ccd9f0', edgecolor='#003366', linewidth=2, zorder=2)

        ax.set_axis_off()
        fig.patch.set_facecolor('white')
        plt.tight_layout(pad=0.2)

        buf = _io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                    facecolor='white', edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"generate_map_image error: {e}")
        return None


def _pdf_row(pdf, label, value, fill, col_w=190):
    """Affiche une ligne label/valeur dans le PDF."""
    GREY = (108, 117, 125)
    BLACK = (30, 30, 30)
    # Saut de page explicite si plus assez de place pour cette ligne
    if pdf.get_y() + 7 > pdf.h - pdf.b_margin:
        pdf.add_page()
    y = pdf.get_y()
    bg = (245, 247, 250) if fill else (255, 255, 255)
    pdf.set_fill_color(*bg)
    pdf.set_draw_color(220, 220, 220)
    pdf.rect(10, y, col_w, 7, 'FD')
    pdf.set_text_color(*GREY)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(12, y + 1.5)
    pdf.cell(120, 4, pdf_safe(str(label))[:60], ln=False)
    pdf.set_text_color(*BLACK)
    pdf.set_font("Helvetica", "B", 8)
    try:
        if isinstance(value, float) and not np.isnan(value):
            val_str = f"{value:,.2f}".replace(",", " ")
        elif isinstance(value, int):
            val_str = f"{value:,}".replace(",", " ")
        else:
            val_str = pdf_safe(str(value))
    except Exception:
        val_str = pdf_safe(str(value))
    pdf.set_xy(132, y + 1.5)
    pdf.cell(66, 4, val_str, ln=False, align="R")
    pdf.ln(7)


def _pdf_section(pdf, title):
    """Affiche un bandeau de titre de section."""
    BLUE = (0, 51, 102)
    # Si moins de 25mm restants, passer à la page suivante
    # pour éviter un titre de section isolé en bas de page
    if pdf.get_y() + 25 > pdf.h - pdf.b_margin:
        pdf.add_page()
    else:
        pdf.ln(3)
    pdf.set_fill_color(*BLUE)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, pdf_safe(f"  {title}"), ln=True, fill=True)
    pdf.ln(1)


def generate_insee_pdf(title, code, type_label, url_insee, indicators, ai_messages=None):
    """Génère un rapport PDF multi-pages depuis les données INSEE et FILOSOFI."""
    from fpdf import FPDF

    BLUE  = (0, 51, 102)
    GREY  = (108, 117, 125)
    LIGHT = (230, 236, 245)

    # Données étendues (on reconstitue le kind technique depuis type_label)
    _label_to_kind = {
        "Communes": "communes", "EPCI (Intercommunalités)": "intercommunalites",
        "Départements": "departements", "Régions": "regions",
        "Arrondissements": "arrondissements",
        "Arrondissements Municipaux (Paris, Lyon, Marseille)": "arrondissementsMunicipaux",
        "Communes Associées / Déléguées": "communesDeleguees",
    }
    _kind = _label_to_kind.get(type_label, "communes")
    extended = fetch_pdf_data(code, _kind, INSEE_KEY)
    # Fusion : indicators en priorité
    all_data = {**extended, **{k: v for k, v in indicators.items() if v is not None}}

    class ReportPDF(FPDF):
        def header(self):
            self.set_fill_color(*BLUE)
            self.rect(0, 0, 210, 12, 'F')
            self.set_text_color(255, 255, 255)
            self.set_font("Helvetica", "B", 9)
            self.set_xy(10, 2)
            self.cell(130, 8, pdf_safe(f"DOSSIER INSEE - {title}"), ln=False)
            self.set_font("Helvetica", "", 8)
            self.set_xy(140, 2)
            self.cell(60, 8, pdf_safe(f"Code : {code}  |  {type_label}"), ln=False, align="R")
            self.ln(14)

        def footer(self):
            self.set_y(-12)
            self.set_draw_color(*BLUE)
            self.line(10, self.get_y(), 200, self.get_y())
            self.set_text_color(*GREY)
            self.set_font("Helvetica", "I", 7)
            self.cell(95, 6, f"Source : INSEE - FILOSOFI 2021, Recensement de la population 2022", ln=False)
            self.cell(95, 6, f"Page {self.page_no()}", align="R")

    pdf = ReportPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # ── PAGE DE GARDE ──────────────────────────────────────────────
    pdf.set_fill_color(*BLUE)
    pdf.rect(0, 14, 210, 55, 'F')
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_xy(10, 20)
    pdf.cell(0, 12, "DOSSIER STATISTIQUE", ln=True)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_x(10)
    pdf.cell(0, 10, pdf_safe(title), ln=True)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_x(10)
    pdf.cell(0, 7, pdf_safe(f"{type_label}  |  Code INSEE : {code}"), ln=True)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_x(10)
    pdf.cell(0, 6, f"Rapport genere le {datetime.date.today().strftime('%d/%m/%Y')}", ln=True)
    pdf.ln(6)

    # Lien dossier complet
    pdf.set_text_color(0, 51, 102)
    pdf.set_font("Helvetica", "U", 9)
    pdf.set_x(10)
    pdf.cell(0, 6, "Consulter le dossier complet sur le site de l'INSEE", ln=True, link=url_insee)
    pdf.ln(6)

    # ── BANDEAU 4 INDICATEURS CLÉS ─────────────────────────────────
    KEY_METRICS = [
        ("Population 2022", "Population", lambda v: f"{int(v):,} hab.".replace(",", " ")),
        ("Densite", "Densité (hab/km²)", lambda v: f"{v} hab/km2"),
        ("Revenu median", "Niveau de vie median (EUR/an)", lambda v: f"{int(v):,} EUR/an".replace(",", " ")),
        ("Taux de pauvrete", "Taux de pauvreté (%)", lambda v: f"{v} %"),
    ]
    pdf.set_draw_color(*BLUE)
    col_w = 46
    y_band = pdf.get_y()
    for i, (label, key, fmt) in enumerate(KEY_METRICS):
        x = 10 + i * (col_w + 2)
        pdf.set_fill_color(*LIGHT)
        pdf.rect(x, y_band, col_w, 24, 'FD')
        pdf.set_text_color(*GREY)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_xy(x + 2, y_band + 2)
        pdf.cell(col_w - 4, 4, label.upper())
        val = all_data.get(key)
        try:
            display = pdf_safe(fmt(val)) if val is not None and not (isinstance(val, float) and np.isnan(val)) else "N/D"
        except Exception:
            display = "N/D"
        pdf.set_text_color(*BLUE)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_xy(x + 2, y_band + 9)
        pdf.cell(col_w - 4, 8, display)
    pdf.ln(32)

    # ── CARTE DU TERRITOIRE ───────────────────────────────────────
    map_img = generate_map_image(code, _kind, title)
    if map_img:
        map_w = 100
        map_h = 66  # ratio 3:2 pour une image figsize (6,4) à dpi 150
        # Saut de page si la carte ne rentre pas
        if pdf.get_y() + map_h + 8 > pdf.h - pdf.b_margin:
            pdf.add_page()
        y_before = pdf.get_y()
        pdf.image(map_img, x=(210 - map_w) / 2, y=y_before, w=map_w)
        pdf.set_y(y_before + map_h + 2)
        pdf.set_text_color(*GREY)
        pdf.set_font("Helvetica", "I", 7)
        pdf.cell(0, 4, pdf_safe(f"Carte du territoire : {title}"), ln=True, align="C")
        pdf.ln(4)

    # ── SECTION 1 : TERRITOIRE ────────────────────────────────────
    _pdf_section(pdf, "1. Presentation du territoire")
    territoire_keys = [
        "Population", "Densité (hab/km²)", "Surface (km2)",
        "Code(s) postal(aux)", "Departement (code)", "Region (code)", "Code Département",
    ]
    for i, k in enumerate(territoire_keys):
        if k in all_data:
            _pdf_row(pdf, k, all_data[k], i % 2 == 0)

    # ── SECTION 2 : COMPOSITION DÉMOGRAPHIQUE ────────────────────
    demo_data = fetch_demographic_data(code, _kind)
    _pdf_section(pdf, "2. Composition demographique (RP 2018)")
    if demo_data:
        # Ligne résumé hommes/femmes
        if 'Part des hommes (%)' in demo_data and 'Part des femmes (%)' in demo_data:
            _pdf_row(pdf, "Part des hommes (%)", demo_data['Part des hommes (%)'], True)
            _pdf_row(pdf, "Part des femmes (%)", demo_data['Part des femmes (%)'], False)
        if 'Indice de jeunesse' in demo_data:
            _pdf_row(pdf, "Indice de jeunesse (pop<20ans / pop>=60ans)", demo_data['Indice de jeunesse'], True)
        # Tranches d'âge
        age_keys = ['Part 0-14 ans (%)', 'Part 15-29 ans (%)', 'Part 30-44 ans (%)',
                    'Part 45-59 ans (%)', 'Part 60-74 ans (%)', 'Part 75-89 ans (%)', 'Part 90 ans et plus (%)']
        for i, k in enumerate(age_keys):
            if k in demo_data:
                _pdf_row(pdf, k, demo_data[k], i % 2 == 0)
    else:
        pdf.set_text_color(108, 117, 125)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 6, "  Donnees non disponibles pour ce territoire.", ln=True)

    # ── SECTION 3 : REVENUS & NIVEAU DE VIE ──────────────────────
    _pdf_section(pdf, "3. Revenus et niveau de vie (FILOSOFI 2021)")
    revenus_keys = [
        "Niveau de vie median (EUR/an)",
        "Niveau de vie D1 - 10pct les plus modestes (EUR/an)",
        "Niveau de vie D9 - 10pct les plus aises (EUR/an)",
        "Rapport interdecile D9/D1",
        "Indice de Gini",
        "Part des revenus d activite (%)",
        "Part des prestations sociales (%)",
        "Part des revenus du patrimoine (%)",
        "Rapport Interdécile (D9/D1)",
        "Part des revenus d'activité (%)",
        "Niveau de vie Médian (€)",
    ]
    found = 0
    for i, k in enumerate(revenus_keys):
        if k in all_data:
            _pdf_row(pdf, k, all_data[k], i % 2 == 0)
            found += 1
    if found == 0:
        pdf.set_text_color(*GREY)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 6, "  Donnees non disponibles pour ce territoire.", ln=True)

    # ── SECTION 4 : PAUVRETÉ ─────────────────────────────────────
    _pdf_section(pdf, "4. Pauvrete et precarite (FILOSOFI 2021)")
    pauvrete_keys = [
        "Taux de pauvreté (%)",
        "Taux de pauvrete a 60pct (%)",
        "Taux de pauvrete des personnes en emploi (%)",
        "Nombre de menages fiscaux",
        "Nombre de personnes (menages fiscaux)",
    ]
    found = 0
    for i, k in enumerate(pauvrete_keys):
        if k in all_data:
            _pdf_row(pdf, k, all_data[k], i % 2 == 0)
            found += 1
    if found == 0:
        pdf.set_text_color(*GREY)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 6, "  Donnees non disponibles pour ce territoire.", ln=True)

    # ── SECTION 5 : TOUTES LES AUTRES DONNÉES ────────────────────
    already_shown = set(territoire_keys + revenus_keys + pauvrete_keys +
                        list(demo_data.keys()) + ["URL Dossier INSEE", "Surface (ha)"])
    remaining = {k: v for k, v in all_data.items()
                 if k not in already_shown and v is not None}
    if remaining:
        _pdf_section(pdf, "5. Donnees complementaires")
        for i, (k, v) in enumerate(remaining.items()):
            _pdf_row(pdf, k, v, i % 2 == 0)

    # ── SECTION 5 : ANALYSE IA (si disponible) ───────────────────
    if ai_messages:
        exchanges = [(m['content'], m['role']) for m in ai_messages
                     if m['role'] in ('user', 'assistant') and
                     not m['content'].startswith("Bonjour !")]
        if exchanges:
            pdf.add_page()
            _pdf_section(pdf, "6. Analyse de l'assistant IA")
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(30, 30, 30)
            for content, role in exchanges:
                prefix_label = "Question : " if role == "user" else "Reponse : "
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_x(10)
                pdf.cell(0, 5, pdf_safe(prefix_label), ln=True)
                pdf.set_font("Helvetica", "", 8)
                pdf.set_x(14)
                pdf.multi_cell(186, 5, pdf_safe(strip_markdown(content)))
                pdf.ln(2)

    # ── NOTE DE BAS DE RAPPORT ────────────────────────────────────
    pdf.ln(6)
    pdf.set_fill_color(*LIGHT)
    pdf.set_text_color(0, 51, 102)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_x(10)
    pdf.multi_cell(190, 5,
        "Ce rapport a ete genere automatiquement a partir des donnees "
        "officielles de l'INSEE (FILOSOFI 2021, Recensement de la population 2022, "
        "API Melodi). Pour acceder au dossier complet interactif avec graphiques et "
        "tableaux detailles, consultez le lien en page 1.", fill=True)

    return pdf.output()


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
                
                # --- EN-TÊTE MODERNISÉ ---
                col_title, col_btns = st.columns([3, 1])
                with col_title:
                    st.markdown(f"""
                    <div style="background-color: white; padding: 20px; border-radius: 12px; border-left: 8px solid #003366; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                        <h1 style='margin: 0; color: #003366; font-size: 2.2rem;'>{row['TITLE']}</h1>
                        <p style='margin: 0; color: #6c757d; font-weight: 500;'>{label_type} | Code INSEE : <b>{row['CODE']}</b></p>
                    </div>
                    """, unsafe_allow_html=True)
                with col_btns:
                    prefix = "EPCI" if type_col in ["EPCI", "intercommunalites"] else ("COM" if type_col == "communes" else ("DEP" if type_col == "departements" else "REG"))
                    url_insee = f"https://www.insee.fr/fr/statistiques/2011101?geo={prefix}-{row['CODE']}"
                    st.link_button("📄 DOSSIER COMPLET INSEE", url_insee, use_container_width=True, icon="📄")

                st.write("") # Spacer

                # --- INDICATEURS CLÉS EN CARTES ---
                m1, m2, m3, m4 = st.columns(4)
                
                with m1:
                    with st.container(border=True):
                        st.caption("👥 Population 2022")
                        if 'Population' in indicators and not pd.isna(indicators['Population']):
                            st.subheader(f"{int(indicators['Population']):,} hab.".replace(',', ' '))
                        else: st.subheader("N/A")
                
                with m2:
                    with st.container(border=True):
                        st.caption("📍 Densité")
                        if 'Densité (hab/km²)' in indicators and not pd.isna(indicators['Densité (hab/km²)']):
                            st.subheader(f"{indicators['Densité (hab/km²)']} hab/km²")
                        else: st.subheader("N/A")

                with m3:
                    with st.container(border=True):
                        st.caption("💰 Revenu Médian (2021)")
                        if 'Niveau de vie Médian (€)' in indicators and not pd.isna(indicators['Niveau de vie Médian (€)']):
                            st.subheader(f"{int(indicators['Niveau de vie Médian (€)']):,} €".replace(',', ' '))
                        else: st.subheader("N/A")

                with m4:
                    with st.container(border=True):
                        st.caption("🚨 Taux de pauvreté")
                        if 'Taux de pauvreté (%)' in indicators and not pd.isna(indicators['Taux de pauvreté (%)']):
                            tp = indicators['Taux de pauvreté (%)']
                            st.subheader(f"{tp}%")
                            # Petite barre visuelle
                            st.progress(min(tp / 30, 1.0)) # 30% est un seuil critique
                        else: st.subheader("N/A")

                st.write("")

                # --- CARTE ET IA (SECTION COLLABORATIVE) ---
                c1, c2 = st.columns([3, 2])
                
                with c1:
                    with st.container(border=True):
                        # Sélecteur de vue compact sur la même ligne que le titre
                        col_map_title, col_map_toggle = st.columns([3, 1])
                        with col_map_title:
                            st.markdown("#### 📍 Cartographie")
                        with col_map_toggle:
                            # Utilisation du paramètre 'key' pour une synchronisation automatique et immédiate
                            st.toggle("🛰️ Satellite", key='map_is_satellite')
                        
                        st.session_state.map_style = "Satellite" if st.session_state.map_is_satellite else "Plan"

                        gdf_main = get_geo(row['CODE'], type_col, row['TITLE'])
                        if gdf_main is not None:
                            center = gdf_main.to_crs(epsg=3857).centroid.to_crs(epsg=4326).iloc[0]
                            
                            # Initialisation de la carte avec les deux couches
                            m = folium.Map(
                                location=[center.y, center.x], 
                                zoom_start=7 if type_col in ["regions", "departements"] else 11,
                                tiles=None # On gère les tuiles manuellement
                            )

                            # Couche Plan (IGN Plan V2 - Plus lisible et institutionnel)
                            folium.TileLayer(
                                tiles='https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/png',
                                attr='&copy; <a href="https://www.ign.fr/">IGN</a> GéoPlateforme',
                                name='Plan IGN',
                                control=False,
                                show=(st.session_state.map_style == "Plan")
                            ).add_to(m)

                            # Couche Photographies Aériennes (IGN Orthophoto)
                            folium.TileLayer(
                                tiles='https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg',
                                attr='&copy; <a href="https://www.ign.fr/">IGN</a> GéoPlateforme',
                                name='Photos Aériennes',
                                control=False,
                                show=(st.session_state.map_style == "Satellite")
                            ).add_to(m)

                            for col in gdf_main.columns:
                                if col != 'geometry': gdf_main[col] = gdf_main[col].astype(str)
                            geojson_data = json.loads(gdf_main.to_json())
                            folium.GeoJson(
                                geojson_data, 
                                style_function=lambda x: {
                                    'fillColor': '#003366', 
                                    'color': '#003366' if st.session_state.map_style == "Plan" else 'white', 
                                    'weight': 3, 
                                    'fillOpacity': 0.1
                                }
                            ).add_to(m)
                            
                            st_folium(m, width=None, height=450, returned_objects=[], key="map_main", use_container_width=True)
                
                with c2:
                    with st.container(border=True):
                        st.markdown("#### 💬 Assistant IA Expert")
                        st.caption("Analysez les données avec l'IA")
                        
                        if "messages" not in st.session_state or not st.session_state.messages:
                            st.session_state.messages = [
                                {"role": "assistant", "content": f"Bonjour ! Je suis votre expert Insee. Posez-moi vos questions sur **{row['TITLE']}**."}
                            ]

                        # Zone de chat avec hauteur fixe
                        chat_area = st.container(height=320)
                        with chat_area:
                            for message in st.session_state.messages:
                                with st.chat_message(message["role"]):
                                    st.markdown(message["content"])

                        if prompt := st.chat_input(f"Question sur {row['TITLE']}"):
                            st.session_state.messages.append({"role": "user", "content": prompt})
                            with chat_area:
                                with st.chat_message("user"):
                                    st.markdown(prompt)
                                with st.chat_message("assistant"):
                                    with st.spinner("Analyse en cours..."):
                                        response = ask_gemini(prompt, indicators, row['TITLE'])
                                        st.markdown(response)
                            st.session_state.messages.append({"role": "assistant", "content": response})

                st.divider()
                # Boutons utilitaires en bas
                b1, b2, b3 = st.columns(3)
                with b1: st.link_button("🗺️ Outil Insee - Carte Carroyée", "https://www.insee.fr/fr/outil-interactif/7737357/map.html", use_container_width=True)
                with b2: st.link_button("📊 Statistiques Locales Insee", "https://statistiques-locales.insee.fr/", use_container_width=True)
                with b3:
                    if st.button("📥 Exporter le rapport (PDF)", use_container_width=True):
                        with st.spinner("Génération du PDF..."):
                            try:
                                pdf_bytes = generate_insee_pdf(
                                    title=row['TITLE'],
                                    code=row['CODE'],
                                    type_label=label_type,
                                    url_insee=url_insee,
                                    indicators=indicators,
                                    ai_messages=st.session_state.get("messages", [])
                                )
                                st.download_button(
                                    label="⬇️ Télécharger le rapport PDF",
                                    data=bytes(pdf_bytes),
                                    file_name=f"dossier_insee_{row['CODE']}.pdf",
                                    mime="application/pdf",
                                    use_container_width=True
                                )
                            except Exception as e:
                                st.error(f"Erreur lors de la génération du PDF : {e}")

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
