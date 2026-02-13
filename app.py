import streamlit as st
import pandas as pd
import requests
import geopandas as gpd
import folium
import json
from streamlit_folium import st_folium
from shapely.geometry import shape

st.set_page_config(page_title="INSEE Finder", layout="wide")

INSEE_KEY = st.secrets.get("INSEE_API_KEY", "dfc20306-246c-477c-8203-06246c977cba")

API_GEO_MAP = {
    "communes": "communes",
    "EPCI": "epcis",
    "departements": "departements",
    "regions": "regions",
}


@st.cache_data
def load_insee(endpt):
    headers = {"Authorization": f"Bearer {INSEE_KEY}", "Accept": "application/json"}
    try:
        r = requests.get(
            f"https://api.insee.fr/metadonnees/geo/{endpt}",
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


@st.cache_data
def get_geojson_raw(code, kind):
    """Récupère le GeoJSON brut depuis l'API Géo Etalab (dict Python)."""
    slug = API_GEO_MAP.get(kind)
    if not slug:
        return None
    try:
        r = requests.get(
            f"https://geo.api.gouv.fr/{slug}/{code}",
            params={"format": "geojson", "geometry": "contour"},
            timeout=15,
        )
        if r.status_code == 200:
            geojson = r.json()
            # Vérifier qu'il y a bien des features
            if geojson.get("features"):
                return geojson
    except Exception:
        pass
    return None


def centroid_from_geojson(geojson):
    """Calcule le centroïde à partir du GeoJSON brut via shapely."""
    try:
        geom = shape(geojson["features"][0]["geometry"])
        c = geom.centroid
        return float(c.y), float(c.x)
    except Exception:
        return 46.6, 2.5  # Centre de la France par défaut


# ── UI ──────────────────────────────────────────────────────────────────

st.title("🗺️ INSEE Finder – Cartographie des territoires")

type_col = st.sidebar.selectbox("Type de collectivité", ["communes", "EPCI", "departements", "regions"])

endpt = "intercommunalites" if type_col == "EPCI" else type_col
data = load_insee(endpt)

if not data:
    st.warning("Impossible de charger les données INSEE. Vérifiez la clé API.")
    st.stop()

df = pd.DataFrame(data)

# Normaliser les colonnes
code_col = "code"
title_col = "intituleComplet" if "intituleComplet" in df.columns else "intitule"
df = df.rename(columns={code_col: "CODE", title_col: "TITLE"})
df["CODE"] = df["CODE"].astype(str)
if type_col == "EPCI":
    df["CODE"] = df["CODE"].str.zfill(9)

search = st.sidebar.text_input("🔍 Rechercher (nom ou code)")

if search:
    mask = df["TITLE"].str.contains(search, case=False, na=False) | df["CODE"].str.contains(search, na=False)
    results = df[mask].head(10)

    if results.empty:
        st.sidebar.info("Aucun résultat.")
        st.stop()

    sel = st.sidebar.selectbox("Choisir un territoire", results["TITLE"].tolist())
    row = results[results["TITLE"] == sel].iloc[0]

    col1, col2 = st.columns([1, 2])

    with col1:
        st.metric("Territoire", row["TITLE"])
        st.write(f"**Code** : `{row['CODE']}`")
        st.write(f"**Type** : {type_col}")

    # Récupérer le GeoJSON brut (pas de round-trip geopandas)
    geojson = get_geojson_raw(row["CODE"], type_col)

    if geojson is not None:
        lat, lon = centroid_from_geojson(geojson)

        with col2:
            m = folium.Map(location=[lat, lon], zoom_start=9)
            folium.GeoJson(
                geojson,
                style_function=lambda x: {
                    "fillColor": "#3388ff",
                    "color": "#3388ff",
                    "weight": 2,
                    "fillOpacity": 0.15,
                },
            ).add_to(m)
            st_folium(m, width=700, height=500, returned_objects=[])
    else:
        col1.error("Contour géographique non trouvé pour ce territoire.")
else:
    st.info("Entrez un nom ou un code dans la barre de recherche pour commencer.")