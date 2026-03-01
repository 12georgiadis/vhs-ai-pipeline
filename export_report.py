# export_report.py
from pathlib import Path
from datetime import datetime


_INTEREST_STARS = {"fort": "★★★", "moyen": "★★☆", "faible": "★☆☆"}
_TYPE_EMOJI     = {
    "revelation":  "🔴",
    "rupture":     "🟠",
    "intime":      "🔵",
    "glitch":      "🟢",
    "banal":       "⚪",
    "detail":      "🔵",
    "transition":  "⚪",
}


def generate_video_report(video_path: Path, analysis: dict, output_path: Path):
    """
    Génère le log de rushes Markdown pour une seule vidéo.
    Format : table de segments + section plans forts + thèmes détectés.
    """
    segments = analysis.get("segments", [])
    profil   = analysis.get("video_profil", {})
    obs      = analysis.get("observations_globales", {})
    meta     = analysis.get("_meta", {})

    model   = meta.get("model", "gemini")
    date_str = datetime.now().strftime("%Y-%m-%d")

    lines = [
        f"# Log de rushes — {video_path.name}",
        f"_Analysé le {date_str} avec {model}_",
        "",
        "## Fiche technique",
        f"| Champ | Valeur |",
        f"|-------|--------|",
        f"| Fichier | `{video_path.name}` |",
        f"| Personnes | {', '.join(profil.get('personnages_presentes', ['?'])) or '?'} |",
        f"| Période estimée | {profil.get('periode_estimee', '?')} |",
        f"| Lieu | {profil.get('lieu', '?')} |",
        f"| Qualité audio | {profil.get('qualite_audio', '?')} |",
        f"| Modèle | {model} |",
        "",
    ]

    # ── Table des segments ─────────────────────────────────────────────────────
    lines += [
        "## Timeline commentée",
        "",
        "| TC Début | TC Fin | Type | Intérêt | Description | Notes monteur |",
        "|----------|--------|------|---------|-------------|---------------|",
    ]
    for seg in segments:
        emoji   = _TYPE_EMOJI.get(seg.get("type_plan", ""), "⚪")
        stars   = _INTEREST_STARS.get(seg.get("interet_film", "moyen"), "★★☆")
        desc    = seg.get("description_visuelle", "").replace("|", "/")[:80]
        notes   = (seg.get("notes_monteur") or "").replace("|", "/")[:60]
        lines.append(
            f"| {seg.get('tc_start','?')} | {seg.get('tc_end','?')} | "
            f"{emoji} {seg.get('type_plan','?')} | {stars} | {desc} | {notes} |"
        )
    lines.append("")

    # ── Transcriptions audio ───────────────────────────────────────────────────
    transcribed = [s for s in segments if s.get("transcription")]
    if transcribed:
        lines += ["## Transcriptions audio", ""]
        for seg in transcribed:
            locuteur = seg.get("locuteur", "?")
            lines.append(f"**{seg['tc_start']}** ({locuteur}) — *\"{seg['transcription']}\"*")
            if seg.get("description_audio"):
                lines.append(f"> {seg['description_audio']}")
            lines.append("")

    # ── Plans forts ───────────────────────────────────────────────────────────
    forts = [s for s in segments if s.get("interet_film") == "fort"]
    if forts:
        lines += ["## Plans forts ★★★", ""]
        for seg in forts:
            emoji = _TYPE_EMOJI.get(seg.get("type_plan", ""), "⚪")
            lines += [
                f"### {emoji} {seg.get('tc_start')} → {seg.get('tc_end')} — {seg.get('type_plan','').upper()}",
                f"**Description** : {seg.get('description_visuelle', '')}",
                f"**Audio** : {seg.get('description_audio', 'N/A')}",
                f"**Interprétation** : {seg.get('interpretation_monteur', '')}",
                f"**Émotion** : {seg.get('emotion_visible') or ''}",
                "",
            ]

    # ── Thèmes détectés ────────────────────────────────────────────────────────
    all_themes: dict[str, int] = {}
    for seg in segments:
        for t in seg.get("themes", []):
            all_themes[t] = all_themes.get(t, 0) + 1
    if all_themes:
        sorted_themes = sorted(all_themes.items(), key=lambda x: -x[1])
        lines += [
            "## Thèmes détectés",
            "",
            " | ".join(f"`{t}` ×{n}" for t, n in sorted_themes[:20]),
            "",
        ]

    # ── Observations globales ──────────────────────────────────────────────────
    if obs:
        lines += ["## Observations du monteur", ""]
        if obs.get("valeur_biographique"):
            lines += [f"**Valeur biographique** : {obs['valeur_biographique']}", ""]
        if obs.get("arcs_detectes"):
            lines += [f"**Arcs narratifs** : {obs['arcs_detectes']}", ""]
        if obs.get("recommandation_monteur"):
            lines += [f"**Recommandation** : {obs['recommandation_monteur']}", ""]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [report] {output_path.name} — {len(segments)} segments, {len(forts)} forts")
    return output_path


def generate_synthesis_report(synthesis_md: str, output_path: Path):
    """Écrit la synthèse globale Gemini dans un fichier Markdown."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"<!-- Généré le {datetime.now().strftime('%Y-%m-%d')} -->\n\n"
    )
    output_path.write_text(header + synthesis_md, encoding="utf-8")
    print(f"  [synthesis] {output_path.name}")
    return output_path
