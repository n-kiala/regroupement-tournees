"""Regroupement des tournées : démo Streamlit.

Entrée : une capture du planning. Sortie : le trajet optimisé par véhicule. Secrets attendus (Settings > Secrets sur Streamlit) :

    ANTHROPIC_API_KEY = "sk-ant-..."     # obligatoire pour lire une capture
    ACCESS_CODE = "un-code-pour-joel"    # facultatif, protège l'accès
    MODEL = "claude-sonnet-5-5"          # facultatif
    GOOGLE_MAPS_API_KEY = "AIza..."      # facultatif : carte Google Maps au lieu d'OpenStreetMap
"""
import base64
import io
import json
import pathlib

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

st.set_page_config(page_title="Regroupement des tournées", page_icon="🚚", layout="wide")

HTML = (pathlib.Path(__file__).parent / "tournees.html").read_text(encoding="utf-8")

PROMPT = """Tu lis une capture d'écran (ou une photo d'écran) du planning d'un logiciel de livraisons d'un loueur de matériel événementiel en Belgique.
Le tableau alterne des bandeaux de tournée (dates, « N° parc : X », « Equipage ») et des lignes de commande. Colonnes d'une ligne, de gauche à droite : Client | Nom du lieu | Ville | code postal à 4 chiffres | numéro d'ordre | métrage (décimal à virgule) | icône | Vers (« Bruxelles » ou « Client ») | Horaires (date puis hh:mm - hh:mm) | numéros de document.
Champs de chaque ligne : client, lieu, ville, cp, metrage (nombre), vers, debut et fin au format HH:MM, tournee, lat, lon, incertain, doc (le premier numéro de document à droite des horaires, chiffres seulement, chaîne vide s'il est illisible).
Règles : une entrée par ligne de commande visible, dans l'ordre de l'écran. "tournee" = le N° parc du bandeau placé au-dessus de la ligne, chaîne vide si le bandeau n'en a pas. "lat" et "lon" = centre approximatif de la commune belge correspondant au code postal, en degrés décimaux. N'invente aucune ligne. Si une cellule est illisible, laisse-la vide et mets "incertain" à true. Si l'image ne contient aucun planning, transmets une liste vide."""


def secret(name, default=""):
    try:
        return st.secrets[name]
    except Exception:
        return default


def prepare_image(uploaded):
    """Réencode la capture en PNG, côté le plus long ramené à 2400 px."""
    img = Image.open(uploaded).convert("RGB")
    longest = max(img.size)
    if longest > 2400:
        k = 2400 / longest
        img = img.resize((round(img.width * k), round(img.height * k)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode()


CHAMPS = {
    "client": {"type": "string"}, "lieu": {"type": "string"}, "ville": {"type": "string"},
    "cp": {"type": "string"}, "metrage": {"type": "number"}, "vers": {"type": "string"},
    "debut": {"type": "string"}, "fin": {"type": "string"}, "tournee": {"type": "string"},
    "lat": {"type": "number"}, "lon": {"type": "number"}, "incertain": {"type": "boolean"}, "doc": {"type": "string"},
}
SCHEMA = {
    "type": "object",
    "properties": {
        "lignes": {
            "type": "array",
            "items": {"type": "object", "properties": CHAMPS, "required": list(CHAMPS), "additionalProperties": False},
        }
    },
    "required": ["lignes"],
    "additionalProperties": False,
}
CONSIGNE_JSON = (
    "\nRéponds UNIQUEMENT par un objet JSON de la forme {\"lignes\": [...]}, sans texte autour. "
    "Chaque ligne contient tous les champs ; une cellule illisible vaut une chaîne vide, ou 0 pour un nombre."
)


def read_capture(uploaded, api_key, model):
    """Lit une capture. La sortie structurée de l'API garantit un JSON valide ; repli sur un JSON libre si elle est refusée."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    contenu = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": prepare_image(uploaded)}},
        {"type": "text", "text": PROMPT + CONSIGNE_JSON},
    ]

    def appel(structure):
        extra = {"output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}} if structure else {}
        resp = client.messages.create(model=model, max_tokens=16000, messages=[{"role": "user", "content": contenu}], **extra)
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("réponse trop longue : découpez la liste en deux captures")
        texte = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        data = json.loads(texte[texte.index("{"): texte.rindex("}") + 1])
        return [l for l in data.get("lignes", []) if isinstance(l, dict)]

    try:
        return appel(True)
    except (anthropic.BadRequestError, TypeError):
        pass  # sortie structurée refusée par ce modèle ou cette version du SDK
    derniere = None
    for _ in range(2):
        try:
            return appel(False)
        except ValueError as e:
            derniere = e
    raise derniere


def fusionner(lots):
    """Réunit les lignes de plusieurs captures. Deux captures qui se recouvrent donnent les mêmes numéros de document : on ne garde la ligne qu'une fois."""
    vus, sortie = set(), []
    for lignes in lots:
        for l in lignes:
            doc = "".join(c for c in str(l.get("doc") or "") if c.isdigit())
            if len(doc) >= 5:
                if doc in vus:
                    continue
                vus.add(doc)
            sortie.append(l)
    return sortie


# Accès protégé par un code, si ACCESS_CODE est défini dans les secrets.
code = secret("ACCESS_CODE")
if code and not st.session_state.get("ok"):
    st.title("Regroupement des tournées")
    saisi = st.text_input("Code d'accès", type="password")
    if saisi == code:
        st.session_state["ok"] = True
        st.rerun()
    elif saisi:
        st.error("Code incorrect.")
    st.stop()

api_key = secret("ANTHROPIC_API_KEY")
model = secret("MODEL", "claude-sonnet-5-5")
st.session_state.setdefault("lignes", [])

st.title("Regroupement des tournées")
st.caption("Déposez la capture d'écran du planning. Le trajet optimisé, regroupé par véhicule, s'affiche en dessous.")

fichiers = st.file_uploader(
    "Capture d'écran du planning",
    type=["png", "jpg", "jpeg", "webp"],
    accept_multiple_files=True,
)

# La lecture se lance toute seule dès qu'une capture est déposée ou changée.
signature = [(f.name, f.size) for f in fichiers] if fichiers else []
if signature != st.session_state.get("signature"):
    st.session_state["signature"] = signature
    st.session_state["lignes"] = []
    if fichiers and not api_key:
        st.session_state["erreur"] = "La clé ANTHROPIC_API_KEY manque dans les secrets de l'application : la lecture des captures est désactivée."
    elif fichiers:
        lots, erreurs = [], []
        for f in fichiers:
            with st.spinner(f"Lecture de {f.name}… (30 secondes environ)"):
                try:
                    lots.append(read_capture(f, api_key, model))
                except Exception as e:  # message lisible plutôt qu'une trace
                    erreurs.append(f"Lecture impossible pour {f.name} : {e}")
        lues = fusionner(lots)
        st.session_state["lignes"] = lues
        if erreurs:
            st.session_state["erreur"] = " ".join(erreurs)
        elif not lues:
            st.session_state["erreur"] = "Aucune ligne de planning reconnue. Essayez une capture plus nette, cadrée sur le tableau."
        else:
            st.session_state["erreur"] = ""
    else:
        st.session_state["erreur"] = ""

if st.session_state.get("erreur"):
    st.error(st.session_state["erreur"])

lignes = st.session_state["lignes"]
if lignes:
    inject = "window.__GMAPS_KEY__=" + json.dumps(secret("GOOGLE_MAPS_API_KEY")) + ";window.__STREAMLIT__=true;window.__ROWS__=" + json.dumps(lignes, ensure_ascii=False).replace("</", "<\\/") + ";"
    components.html(HTML.replace("/*__INJECT__*/", inject), height=2200, scrolling=True)
