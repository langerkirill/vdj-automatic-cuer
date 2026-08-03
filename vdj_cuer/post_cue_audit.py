"""Post-cue visual audit: waveform image + beatgrid comparison after each write."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from vdj_audit.audio import analyze_audio
from vdj_audit.database import load_single_track
from vdj_audit.inspection import inspect_track
from vdj_audit.reports import render_svg, safe_slug


DEFAULT_POST_CUE_AUDIT_ROOT = Path(__file__).resolve().parent.parent / "audit_reports" / "auto"


class PostCueAuditMixin:
    """Generate track audit images and grid checks after successful cue writes."""

    def _post_cue_audit_enabled(self) -> bool:
        return bool(getattr(self, "post_cue_audit_enabled", True))

    def _post_cue_audit_root(self) -> Path:
        root = getattr(self, "post_cue_audit_dir", None)
        if root:
            return Path(root)
        return DEFAULT_POST_CUE_AUDIT_ROOT

    def _ensure_post_cue_run_dir(self) -> Path:
        run_dir = getattr(self, "_post_cue_run_dir", None)
        if run_dir is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = self._post_cue_audit_root() / stamp
            run_dir.mkdir(parents=True, exist_ok=True)
            self._post_cue_run_dir = run_dir
            self._post_cue_audit_entries: List[dict] = []
            print(f"🖼️  Post-cue audits → {run_dir}")
        return run_dir

    def audit_track_after_cue(
        self, audio_file_path: str, *, dry_run: bool = False
    ) -> Optional[dict]:
        """
        After a successful database write, render a waveform image and compare
        cues against the VirtualDJ beatgrid.

        Returns a summary dict, or None when audit is disabled/skipped.
        """
        if dry_run or not self._post_cue_audit_enabled():
            return None

        run_dir = self._ensure_post_cue_run_dir()
        database_path = Path(self.vdj_database_path)
        track_name = Path(audio_file_path).name

        try:
            track = load_single_track(database_path, audio_file_path)
            if track is None:
                print(f"⚠️  Post-cue audit skipped (not in DB): {track_name}")
                return None

            analysis = analyze_audio(track, bins=1200)
            observations, issues = inspect_track(track, analysis)
            grid_issues = [
                issue for issue in issues if issue.elements == "grid" or "downbeat" in issue.issue.lower() or "beat grid" in issue.issue.lower() or "Phase" in issue.issue
            ]

            index = len(getattr(self, "_post_cue_audit_entries", [])) + 1
            svg_name = f"{index:02d}-{safe_slug(Path(audio_file_path).stem)}.svg"
            render_svg(track, analysis, issues, run_dir / svg_name)

            # Per-track issue list for quick review.
            issue_path = run_dir / f"{index:02d}-{safe_slug(Path(audio_file_path).stem)}-issues.tsv"
            with issue_path.open("w", encoding="utf-8") as handle:
                handle.write(
                    "track\tcue\ttimestamp\tseverity\tissue\tcue_color\texpected_color\telements\n"
                )
                for issue in issues:
                    handle.write(
                        f"{issue.track}\t{issue.cue}\t{issue.timestamp:.3f}\t"
                        f"{issue.severity}\t{issue.issue}\t{issue.cue_color}\t"
                        f"{issue.expected_color}\t{issue.elements}\n"
                    )

            summary = {
                "track": track_name,
                "path": audio_file_path,
                "svg": svg_name,
                "issues": len(issues),
                "grid_issues": len(grid_issues),
                "cues": len(track.pois),
                "beatgrid": track.beatgrid,
                "scan_phase": track.scan_phase,
            }
            self._post_cue_audit_entries.append(summary)
            self._write_post_cue_index()

            if grid_issues:
                print(
                    f"🖼️  Audit {track_name}: {len(issues)} issue(s) "
                    f"({len(grid_issues)} grid) → {svg_name}"
                )
                for issue in grid_issues[:3]:
                    print(f"   ⚠️  {issue.cue} @ {issue.timestamp:.1f}s: {issue.issue}")
            elif issues:
                print(
                    f"🖼️  Audit {track_name}: {len(issues)} review issue(s) → {svg_name}"
                )
            else:
                print(f"🖼️  Audit {track_name}: clean (on-grid) → {svg_name}")

            return summary
        except Exception as error:
            print(f"⚠️  Post-cue audit failed for {track_name}: {error}")
            return None

    def _write_post_cue_index(self) -> None:
        run_dir = getattr(self, "_post_cue_run_dir", None)
        entries = getattr(self, "_post_cue_audit_entries", None)
        if not run_dir or not entries:
            return

        total_issues = sum(item["issues"] for item in entries)
        total_grid = sum(item["grid_issues"] for item in entries)
        parts = [
            "<!doctype html><meta charset='utf-8'><title>Post-Cue Audit</title>",
            "<style>"
            "body{font-family:Arial;background:#0f172a;color:#e5e7eb;padding:24px}"
            "a{color:#93c5fd}.bad{color:#fca5a5}.ok{color:#86efac}"
            "table{border-collapse:collapse;width:100%}"
            "td,th{border-bottom:1px solid #334155;padding:8px;text-align:left}"
            "</style>",
            "<h1>Post-Cue Visual Audit</h1>",
            f"<p>Tracks: {len(entries)} · Issues: {total_issues} · Grid issues: {total_grid}</p>",
            "<table><tr><th>#</th><th>Track</th><th>Cues</th><th>Issues</th><th>Grid</th><th>Beatgrid</th></tr>",
        ]
        for index, item in enumerate(entries, 1):
            klass = "bad" if item["issues"] else "ok"
            bg = (
                f"{item['beatgrid']:.3f}s"
                if item.get("beatgrid") is not None
                else "—"
            )
            parts.append(
                "<tr>"
                f"<td>{index}</td>"
                f"<td class='{klass}'><a href='{html.escape(item['svg'])}'>"
                f"{html.escape(item['track'])}</a></td>"
                f"<td>{item['cues']}</td>"
                f"<td>{item['issues']}</td>"
                f"<td>{item['grid_issues']}</td>"
                f"<td>{bg}</td>"
                "</tr>"
            )
        parts.append("</table>")
        (run_dir / "index.html").write_text("\n".join(parts), encoding="utf-8")

        # Append-only machine-readable summary for the run.
        summary_path = run_dir / "summary.tsv"
        with summary_path.open("w", encoding="utf-8") as handle:
            handle.write(
                "track\tcues\tissues\tgrid_issues\tbeatgrid\tscan_phase\tsvg\n"
            )
            for item in entries:
                handle.write(
                    f"{item['track']}\t{item['cues']}\t{item['issues']}\t"
                    f"{item['grid_issues']}\t{item.get('beatgrid')}\t"
                    f"{item.get('scan_phase')}\t{item['svg']}\n"
                )
