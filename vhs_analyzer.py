#!/usr/bin/env python3
# vhs_analyzer.py — Archive footage AI analysis pipeline
#
# Usage:
#   python vhs_analyzer.py /path/to/footage/
#   python vhs_analyzer.py video.mp4
#   python vhs_analyzer.py /footage/ --blind       # enable blind pass (2a)
#   python vhs_analyzer.py /footage/ --resume
#   python vhs_analyzer.py /footage/ --dry-run
#   python vhs_analyzer.py /footage/ --retry-failed
#   python vhs_analyzer.py /footage/ --phase 4     # export only (JSON already present)
#   python vhs_analyzer.py /footage/ --no-proxy

import argparse
import json
import os
import sys
from pathlib import Path

import config
from analyze import (
    analyze_segment,
    analyze_segment_blind,
    generate_synthesis,
    merge_chunk_results,
    preanalyze_video,
)
from export_fcpxml import generate_fcpxml
from export_report import generate_synthesis_report, generate_video_report
from preprocess import extract_sample, get_duration, prepare_video
from progress import ProgressTracker, with_retry


# ── Helpers ────────────────────────────────────────────────────────────────────

def collect_videos(source: Path) -> list[Path]:
    """Collecte tous les fichiers vidéo depuis un fichier ou un dossier."""
    if source.is_file():
        return [source] if source.suffix.lower() in config.VIDEO_EXTENSIONS else []
    return sorted(
        p for p in source.rglob("*")
        if p.suffix.lower() in config.VIDEO_EXTENSIONS
        and not p.name.startswith("._")           # ignore les métadonnées macOS
        and "vhs_analysis_output" not in str(p)   # ignore le dossier output
    )


def estimate_cost(videos: list[Path]) -> None:
    """Affiche une estimation de coût (modèles courants feb 2026)."""
    total_s = 0
    for v in videos:
        try:
            total_s += get_duration(v)
        except Exception:
            total_s += 3600  # suppose 1h si ffprobe échoue

    total_h = total_s / 3600
    # ~92 tokens/s avec proxy 1fps (approximation prudente)
    tokens = total_s * 92
    # Flash preview input + output (output verbeux = ~4x l'input)
    cost_flash = tokens * (0.50 + 3.00 * 4) / 1_000_000
    cost_blind  = tokens * (0.50 + 3.00 * 1) / 1_000_000  # output minimal
    cost_deep   = tokens * 0.10 * (1.25 / 1_000_000)      # ~10% du corpus en deep pass

    print(f"\n  Vidéos     : {len(videos)}")
    print(f"  Durée tot. : {total_h:.1f}h ({total_s/60:.0f} min)")
    print(f"  Analyse (phase 2)    : ~${cost_flash:.2f}")
    print(f"  + passe aveugle (2a) : ~${cost_blind:.2f}")
    print(f"  + deep pass (phase 3): ~${cost_deep:.2f}")
    print(f"  Total estimé         : ~${cost_flash + cost_blind + cost_deep:.2f} USD (±40%)")
    print()


# ── Phases ─────────────────────────────────────────────────────────────────────

def phase_preanalysis(videos: list[Path], work_dir: Path, tracker: ProgressTracker) -> dict:
    """Phase 1 : pré-analyse rapide d'un échantillon de chaque vidéo."""
    corpus_file = work_dir / config.CORPUS_FILE
    if corpus_file.exists():
        print("[Phase 1] corpus_context.json déjà présent → skip")
        return json.loads(corpus_file.read_text())

    print("\n[Phase 1] Pré-analyse du corpus ...")
    corpus = {}

    for video in videos:
        print(f"\n  {video.name}")
        sample = extract_sample(video, work_dir)

        def do_preanalysis():
            return preanalyze_video(sample)

        result = with_retry(do_preanalysis, f"preanalysis_{video.name}", tracker)
        if result:
            corpus[video.name] = result
            print(f"  Granularité : {result.get('granularite_recommandee_secondes', '?')}s"
                  f"  | Type : {result.get('type_materiau', '?')}"
                  f"  | Qualité : {result.get('qualite_image', '?')}")

    corpus_file.write_text(json.dumps(corpus, indent=2, ensure_ascii=False))
    print(f"\n[Phase 1] corpus_context.json écrit ({len(corpus)} vidéos)")
    return corpus


def phase_blind_analysis(
    videos: list[Path],
    work_dir: Path,
    tracker: ProgressTracker,
    skip_proxy: bool = False,
) -> dict[str, dict]:
    """
    Phase 2a : passe aveugle — aucun cadre théorique.
    Retourne {video_name: blind_analysis_dict} et écrit dans blind_analysis/.
    """
    print("\n[Phase 2a] Passe aveugle (sans cadre théorique) ...")
    blind_dir = work_dir / "blind_analysis"
    blind_dir.mkdir(parents=True, exist_ok=True)
    all_blind = {}

    for video in videos:
        name = video.name
        blind_path = blind_dir / f"{video.stem}.json"

        if blind_path.exists():
            print(f"\n  {name} → passe aveugle déjà faite, chargement ...")
            all_blind[name] = json.loads(blind_path.read_text())
            continue

        print(f"\n  {name} (aveugle)")

        if skip_proxy:
            segments_to_process = [{"path": video, "start_s": 0}]
        else:
            segments_to_process = prepare_video(video, work_dir)

        chunk_results = []
        for seg in segments_to_process:
            proxy_path = seg["path"]
            start_s    = seg["start_s"]

            def do_blind(p=proxy_path, s=start_s):
                return analyze_segment_blind(p, start_s=s)

            result = with_retry(do_blind, f"{name}_blind_{start_s}", tracker)
            if result:
                chunk_results.append(result)

        if not chunk_results:
            continue

        merged = merge_chunk_results(chunk_results)
        blind_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
        all_blind[name] = merged

        n_fort = sum(1 for s in merged.get("segments", []) if s.get("interet_film") == "fort")
        print(f"  → {len(merged.get('segments', []))} segments, {n_fort} forts (aveugle)")

    return all_blind


def phase_analysis(
    videos: list[Path],
    work_dir: Path,
    tracker: ProgressTracker,
    skip_proxy: bool = False,
) -> dict[str, dict]:
    """Phase 2b : analyse profonde avec les 12 signaux."""
    print("\n[Phase 2b] Analyse profonde (12 signaux) ...")
    raw_dir = work_dir / "raw_json"
    raw_dir.mkdir(parents=True, exist_ok=True)
    all_analyses = {}

    for video in videos:
        name = video.name
        raw_path = raw_dir / f"{video.stem}.json"

        if raw_path.exists():
            print(f"\n  {name} → déjà analysé, chargement ...")
            all_analyses[name] = json.loads(raw_path.read_text())
            continue

        if tracker.is_done(name):
            print(f"\n  {name} → checkpoint OK, skip")
            continue

        print(f"\n  {name}")

        if skip_proxy:
            segments_to_process = [{"path": video, "start_s": 0}]
        else:
            segments_to_process = prepare_video(video, work_dir)

        chunk_results = []
        for seg in segments_to_process:
            proxy_path = seg["path"]
            start_s    = seg["start_s"]

            def do_analysis(p=proxy_path, s=start_s):
                return analyze_segment(p, start_s=s)

            result = with_retry(do_analysis, f"{name}_chunk_{start_s}", tracker)
            if result:
                chunk_results.append(result)

        if not chunk_results:
            continue

        merged = merge_chunk_results(chunk_results)
        merged["_source_file"] = str(video)
        raw_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
        tracker.mark_done(name)
        all_analyses[name] = merged

        n_seg   = len(merged.get("segments", []))
        n_forts = sum(1 for s in merged.get("segments", []) if s.get("interet_film") == "fort")
        print(f"  → {n_seg} segments, {n_forts} forts")

    return all_analyses


def enrich_with_blind(all_analyses: dict, all_blind: dict) -> dict:
    """
    Enrichit chaque segment de l'analyse ciblée avec les observations de la passe aveugle.
    Match par tc_start. Ajoute 3 champs : blind_ce_qui_me_retient, blind_tension_visible,
    blind_description_pure.
    """
    for name, analysis in all_analyses.items():
        blind = all_blind.get(name, {})
        if not blind:
            continue
        blind_by_tc = {
            s["tc_start"]: s
            for s in blind.get("segments", [])
            if s.get("tc_start")
        }
        for seg in analysis.get("segments", []):
            tc = seg.get("tc_start")
            if tc and tc in blind_by_tc:
                b = blind_by_tc[tc]
                seg["blind_ce_qui_me_retient"] = b.get("ce_qui_me_retient")
                seg["blind_tension_visible"]   = b.get("tension_visible")
                seg["blind_description_pure"]  = b.get("description_pure")
    return all_analyses


def phase_export(videos: list[Path], all_analyses: dict, work_dir: Path):
    """Phase 4 : génère FCPXML + rapports MD pour chaque vidéo."""
    print("\n[Phase 4] Export FCPXML + rapports ...")
    fcpxml_dir  = work_dir / "fcpxml"
    reports_dir = work_dir / "reports"

    for video in videos:
        name     = video.name
        analysis = all_analyses.get(name)
        if not analysis:
            print(f"  {name} → pas d'analyse, skip export")
            continue

        generate_fcpxml(
            video_path=video,
            analysis=analysis,
            output_path=fcpxml_dir / f"{video.stem}.fcpxml",
        )
        generate_video_report(
            video_path=video,
            analysis=analysis,
            output_path=reports_dir / f"{video.stem}_log.md",
        )


def phase_synthesis(all_analyses: dict, work_dir: Path):
    """Phase 5 : synthèse globale du corpus."""
    output_path = work_dir / "SYNTHESIS.md"
    if output_path.exists():
        print("\n[Synthesis] Already exists → skip (delete the file to regenerate)")
        return

    print("\n[Synthesis] Generating corpus overview ...")
    synthesis_md = generate_synthesis(list(all_analyses.values()))
    generate_synthesis_report(synthesis_md, output_path)
    print(f"  → {output_path}")


# ── CLI principal ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI-powered archive footage analysis pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python vhs_analyzer.py /Volumes/USB321FD/
  python vhs_analyzer.py video.mp4 --blind
  python vhs_analyzer.py /dossier/ --resume
  python vhs_analyzer.py /dossier/ --dry-run
  python vhs_analyzer.py /dossier/ --retry-failed
  python vhs_analyzer.py /dossier/ --phase 4
        """,
    )
    parser.add_argument("source", help="Dossier ou fichier vidéo à analyser")
    parser.add_argument("--blind", action="store_true",
                        help="Active la passe aveugle (phase 2a) avant l'analyse ciblée")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simule le pipeline sans appeler l'API")
    parser.add_argument("--resume", action="store_true",
                        help="Reprend depuis le dernier checkpoint")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retente uniquement les vidéos en échec")
    parser.add_argument("--phase", type=int, default=1, choices=[1, 2, 3, 4, 5],
                        help="Démarre à la phase N (1=tout, 4=export seul, 5=synthèse seule)")
    parser.add_argument("--no-proxy", action="store_true",
                        help="Skip la compression proxy (vidéo déjà en 480p ou test rapide)")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        print(f"ERREUR : chemin introuvable : {source}")
        sys.exit(1)

    local_output_base = source.parent / "vhs-analysis" if source.is_file() else source / "vhs-analysis"
    work_dir = local_output_base if source.is_dir() else local_output_base / source.stem
    work_dir.mkdir(parents=True, exist_ok=True)

    videos = collect_videos(source)
    if not videos:
        print(f"Aucun fichier vidéo trouvé dans : {source}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  VHS Archive Analyzer")
    print(f"{'='*60}")
    print(f"  Source  : {source}")
    print(f"  Output  : {work_dir}")

    if args.blind:
        print(f"  Mode    : passe aveugle + analyse ciblée")

    tracker = ProgressTracker(work_dir)

    if args.retry_failed:
        failed_names = set(tracker.failed_files)
        videos = [v for v in videos if v.name in failed_names]
        if not videos:
            print("Aucune vidéo en échec à relancer.")
            sys.exit(0)
        print(f"  Mode    : retry-failed ({len(videos)} vidéos)")
        for v in videos:
            tracker._data["failed"] = [
                f for f in tracker._data["failed"] if f["file"] != v.name
            ]
        tracker._save()
    else:
        print(f"  Vidéos  : {len(videos)}")

    estimate_cost(videos)

    if args.dry_run:
        print("[DRY-RUN] Simulation terminée. Aucun appel API effectué.")
        for v in videos:
            print(f"  → {v.name}")
        sys.exit(0)

    if not config.GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY missing.")
        print("Set it: export GEMINI_API_KEY=your-key")
        print("Get a key at: https://aistudio.google.com/apikey")
        sys.exit(1)

    all_analyses = {}
    all_blind    = {}

    # ── Phase 1 : pré-analyse ──────────────────────────────────────────────────
    if args.phase <= 1:
        phase_preanalysis(videos, work_dir, tracker)

    # ── Phase 2a : passe aveugle (optionnelle) ─────────────────────────────────
    if args.phase <= 2 and args.blind:
        all_blind = phase_blind_analysis(videos, work_dir, tracker, skip_proxy=args.no_proxy)

    # ── Phase 2b : analyse ciblée (12 signaux) ─────────────────────────────────
    if args.phase <= 3:
        all_analyses = phase_analysis(videos, work_dir, tracker, skip_proxy=args.no_proxy)
    else:
        raw_dir = work_dir / "raw_json"
        for video in videos:
            raw_path = raw_dir / f"{video.stem}.json"
            if raw_path.exists():
                all_analyses[video.name] = json.loads(raw_path.read_text())

    # ── Enrichissement avec la passe aveugle ───────────────────────────────────
    if all_blind and all_analyses:
        print("\n[Merge] Enrichissement avec les observations aveugles ...")
        all_analyses = enrich_with_blind(all_analyses, all_blind)
        # Réécriture des JSONs enrichis
        raw_dir = work_dir / "raw_json"
        for name, analysis in all_analyses.items():
            stem = Path(name).stem
            raw_path = raw_dir / f"{stem}.json"
            raw_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
        print(f"  → {len(all_analyses)} vidéos enrichies")

    # ── Phase 4 : export FCPXML + rapports ─────────────────────────────────────
    if args.phase <= 4:
        phase_export(videos, all_analyses, work_dir)

    # ── Phase 5 : synthèse globale ─────────────────────────────────────────────
    if args.phase <= 5 and all_analyses:
        phase_synthesis(all_analyses, work_dir)

    print(f"\n{'='*60}")
    print(f"  Terminé — {tracker.summary()}")
    print(f"  Output : {work_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
