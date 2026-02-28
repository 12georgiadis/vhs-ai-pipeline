# analyze.py
import json
import time
from pathlib import Path

from google import genai
from google.genai import types

import config
from prompts import SYSTEM_BLIND_PASS, SYSTEM_DEEP_ANALYSIS, SYSTEM_PREANALYSIS, SYSTEM_SYNTHESIS


def _client() -> genai.Client:
    if not config.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY manquante. Source ~/.claude/secrets/opc-skills.env avant de lancer."
        )
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _upload_video(client: genai.Client, video_path: Path) -> types.File:
    """Upload un fichier vidéo via la Files API. Attend la fin du traitement."""
    print(f"    [upload] {video_path.name} ({video_path.stat().st_size // 1024 // 1024}MB) ...")
    with open(video_path, "rb") as f:
        file_obj = client.files.upload(
            file=f,
            config={"display_name": video_path.stem, "mime_type": "video/mp4"},
        )

    # Attendre que Gemini finisse de traiter le fichier
    while file_obj.state.name == "PROCESSING":
        print(f"    [upload] Traitement Gemini en cours ...")
        time.sleep(5)
        file_obj = client.files.get(name=file_obj.name)

    if file_obj.state.name == "FAILED":
        raise RuntimeError(f"Upload échoué pour {video_path.name}: {file_obj.state}")

    print(f"    [upload] OK → {file_obj.uri}")
    return file_obj


def _delete_file(client: genai.Client, file_obj: types.File):
    """Supprime le fichier de Google Files API après traitement."""
    try:
        client.files.delete(name=file_obj.name)
    except Exception:
        pass  # Non-critique


def _parse_json_response(text: str) -> dict:
    """
    Extrait le JSON de la réponse Gemini, qui peut contenir du texte autour.
    Cherche le premier { ou [ et le dernier } ou ].
    """
    # Nettoie les balises markdown si présentes
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # Extrait le JSON brut
    start = min(
        (text.find(c) for c in ["{", "["] if c in text),
        default=-1,
    )
    end = max(text.rfind("}"), text.rfind("]"))

    if start == -1 or end == -1:
        raise ValueError(f"Pas de JSON dans la réponse : {text[:200]}")

    return json.loads(text[start : end + 1])


def preanalyze_video(video_path: Path) -> dict:
    """
    Phase 1 : pré-analyse rapide sur un échantillon.
    Retourne un dict de profil (contexte, granularité, personnages...).
    """
    client = _client()
    file_obj = _upload_video(client, video_path)

    try:
        response = client.models.generate_content(
            model=config.MODEL_PREANALYSIS,
            contents=[
                types.Part.from_uri(file_uri=file_obj.uri, mime_type="video/mp4"),
                types.Part.from_text(text=SYSTEM_PREANALYSIS),
            ],
        )
        return _parse_json_response(response.text)
    finally:
        _delete_file(client, file_obj)


def analyze_segment(video_path: Path, start_s: float = 0, model: str = None) -> dict:
    """
    Phase 2 : analyse profonde d'un segment (proxy complet ou chunk).
    Retourne le JSON structuré avec segments, audio, interprétations.

    start_s : offset en secondes pour reconstruire les TC absolus.
    """
    client = _client()
    model = model or config.MODEL_ANALYSIS
    file_obj = _upload_video(client, video_path)

    # Contexte de décalage temporel pour les chunks
    offset_note = ""
    if start_s > 0:
        h, r = divmod(int(start_s), 3600)
        m, s = divmod(r, 60)
        offset_note = f"\n\nIMPORTANT : cette vidéo est un segment qui commence à {h:02d}:{m:02d}:{s:02d} \
dans la vidéo originale. Le timecode brûlé dans l'image est l'heure absolue — utilise-le directement."

    # Résolution vidéo : LOW sur 2.5-pro uniquement (réduit tokens, permet 6h/appel)
    gen_config = None
    if model == config.MODEL_DEEP_PASS and config.USE_LOW_RESOLUTION:
        gen_config = types.GenerateContentConfig(
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW
        )

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_uri(file_uri=file_obj.uri, mime_type="video/mp4"),
                types.Part.from_text(text=SYSTEM_DEEP_ANALYSIS + offset_note),
            ],
            config=gen_config,
        )
        result = _parse_json_response(response.text)
        result["_meta"] = {
            "model": model,
            "proxy_file": video_path.name,
            "start_s": start_s,
        }
        return result
    finally:
        _delete_file(client, file_obj)


def analyze_segment_blind(video_path: Path, start_s: float = 0, model: str = None) -> dict:
    """
    Phase 2a : passe aveugle — aucun cadre théorique.
    Le modèle décrit ce qu'il voit sans contexte sur le sujet du film.
    Les résultats enrichissent ensuite les segments de la passe ciblée (par tc_start).
    """
    client = _client()
    model = model or config.MODEL_BLIND_PASS
    file_obj = _upload_video(client, video_path)

    offset_note = ""
    if start_s > 0:
        h, r = divmod(int(start_s), 3600)
        m, s = divmod(r, 60)
        offset_note = (
            f"\n\nIMPORTANT : ce segment commence à {h:02d}:{m:02d}:{s:02d} dans la vidéo "
            "originale. Le timecode brûlé est l'heure absolue — utilise-le directement."
        )

    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_uri(file_uri=file_obj.uri, mime_type="video/mp4"),
                types.Part.from_text(text=SYSTEM_BLIND_PASS + offset_note),
            ],
        )
        result = _parse_json_response(response.text)
        result["_meta"] = {
            "model": model,
            "proxy_file": video_path.name,
            "start_s": start_s,
            "pass": "blind",
        }
        return result
    finally:
        _delete_file(client, file_obj)


def merge_chunk_results(chunk_results: list[dict]) -> dict:
    """
    Fusionne les résultats de plusieurs chunks d'une même vidéo en un seul objet.
    Déduplique les segments qui chevauchent la zone d'overlap.
    """
    if len(chunk_results) == 1:
        return chunk_results[0]

    merged = {
        "video_profil": chunk_results[0].get("video_profil", {}),
        "segments": [],
        "observations_globales": {},
    }

    seen_starts = set()
    for result in chunk_results:
        for seg in result.get("segments", []):
            tc = seg.get("tc_start", "")
            if tc not in seen_starts:
                seen_starts.add(tc)
                merged["segments"].append(seg)

    # Observations globales : garder la dernière (la plus complète)
    merged["observations_globales"] = chunk_results[-1].get("observations_globales", {})
    return merged


def generate_synthesis(all_analyses: list[dict]) -> str:
    """
    Phase finale : synthèse globale du corpus.
    Envoie tous les JSONs d'analyse à Gemini et demande une synthèse narrative.
    Retourne du Markdown.
    """
    client = _client()

    # Compacte les analyses pour ne pas dépasser le contexte
    corpus_text = json.dumps(all_analyses, ensure_ascii=False, indent=2)

    # Si trop long, on tronque en gardant les segments forts
    if len(corpus_text) > 800_000:
        # Garde uniquement les segments à fort intérêt
        compact = []
        for analysis in all_analyses:
            a = dict(analysis)
            a["segments"] = [
                s for s in analysis.get("segments", [])
                if s.get("interet_film") == "fort"
            ]
            compact.append(a)
        corpus_text = json.dumps(compact, ensure_ascii=False, indent=2)

    response = client.models.generate_content(
        model=config.MODEL_ANALYSIS,
        contents=[
            types.Part.from_text(text=SYSTEM_SYNTHESIS),
            types.Part.from_text(text=f"\n\nVoici les analyses du corpus :\n\n{corpus_text}"),
        ],
    )
    return response.text
