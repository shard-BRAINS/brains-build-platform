"""Render the markdown PMO dashboard from current state."""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from importlib.resources import files
from pathlib import Path

from jinja2 import Template, select_autoescape

from build_platform.audit import load_audit_index
from build_platform.paths import state_dir
from build_platform.schemas import WPState
from build_platform.state import (
    load_deliverables,
    load_project,
    load_work_packages,
    load_workstreams,
)


def _template(filename: str = "dashboard.md.j2") -> Template:
    src = files("build_platform.templates").joinpath(filename).read_text(encoding="utf-8")
    return Template(src, autoescape=select_autoescape(), keep_trailing_newline=True)


_DECISION_HEADER = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s+—\s+(.+?)\s*$")


def _recent_decisions(project_root: Path, within_days: int = 7) -> list[dict]:
    """Return decisions logged in the last ``within_days`` days, newest first.

    Parses ``.brains-build/decisions.md`` — the ledger appended by
    ``/build-decision`` — where each entry has the form::

        ## YYYY-MM-DD — <title>
        **Owner:** <owner>
        ...
        **Related WPs:** <related>

    Returns ``[]`` when the ledger is absent. This is the dashboard's
    "Recent decisions (last 7 days)" section — the record of *why* the
    architecture is what it is, so an empty list here means no decisions
    were logged in the window, not that logging is broken.
    """
    ledger = state_dir(project_root) / "decisions.md"
    if not ledger.exists():
        return []

    def _field(block: list[str], name: str) -> str:
        prefix = f"**{name}:**"
        for line in block:
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""

    today = datetime.now(timezone.utc).date()
    lines = ledger.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if _DECISION_HEADER.match(ln)]
    out: list[dict] = []
    for idx, start in enumerate(starts):
        m = _DECISION_HEADER.match(lines[start])
        assert m is not None  # guaranteed by the starts filter
        try:
            logged = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        age = (today - logged).days
        if age < 0 or age > within_days:
            continue
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        block = lines[start + 1:end]
        out.append({
            "date": m.group(1),
            "title": m.group(2),
            "owner": _field(block, "Owner") or "unknown",
            "related": _field(block, "Related WPs") or "_None_",
        })
    out.sort(key=lambda e: e["date"], reverse=True)
    return out


def _sprint_number(project_root: Path) -> int:
    sprints = sorted((state_dir(project_root) / "sprints").glob("sprint-*.md"))
    return len(sprints) + 1 if not sprints else len(sprints)


def _day_of_sprint(project_root: Path) -> int:
    sprints = sorted((state_dir(project_root) / "sprints").glob("sprint-*.md"))
    if not sprints:
        return 1
    last = sprints[-1].stat().st_mtime
    delta = (datetime.now(timezone.utc).timestamp() - last) / 86400
    return max(1, int(delta) + 1)


def _live(project_root: Path) -> list[str]:
    runs = state_dir(project_root) / "runs"
    if not runs.exists():
        return []
    out = []
    cutoff = datetime.now(timezone.utc).timestamp() - 3600  # 1h sliding window
    for run_dir in runs.iterdir():
        if not run_dir.is_dir():
            continue
        if run_dir.stat().st_mtime < cutoff:
            continue
        out.append(f"{run_dir.name} · started {datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat(timespec='seconds')}")
    return out


def _assemble_context(project_root: Path) -> dict:
    """Compute the data the dashboard templates render. Shared between md + html."""
    project = load_project(project_root)
    deliverables = load_deliverables(project_root)
    workstreams = load_workstreams(project_root)
    wps = load_work_packages(project_root)

    by_state = Counter(wp.state for wp in wps)
    health = {
        "active": by_state.get(WPState.DEFINED, 0) + by_state.get(WPState.DISPATCHED, 0) + by_state.get(WPState.IN_REVIEW, 0),
        "blocked": by_state.get(WPState.BLOCKED, 0),
        "done_this_sprint": by_state.get(WPState.DONE, 0),
        "velocity": 0,
        "user_blockers": by_state.get(WPState.BLOCKED, 0),
    }

    sorted_deliverables = sorted(deliverables, key=lambda d: d.sequence)
    deliverable_sequence = " ▶ ".join(d.id for d in sorted_deliverables) or None
    done_count = sum(1 for d in sorted_deliverables if d.state == "done")
    progress_pct = int(done_count * 100 / len(sorted_deliverables)) if sorted_deliverables else 0
    current_focus_d = next((d for d in sorted_deliverables if d.state == "in_progress"), None)
    current_focus = f"{current_focus_d.id} ({current_focus_d.title})" if current_focus_d else None
    next_milestone = None
    if current_focus_d:
        open_wps = [wp for wp in wps if wp.deliverable_id == current_focus_d.id and wp.state != WPState.DONE]
        if open_wps:
            next_milestone = f"{current_focus_d.id} acceptance review (est. on {', '.join(w.id for w in open_wps[:2])} completion)"
    next_action = None
    next_defined = next((wp for wp in wps if wp.state == WPState.DEFINED), None)
    if next_defined:
        next_action = f"dispatch {next_defined.id} ({next_defined.title})"

    wp_by_deliverable: dict[str, list] = defaultdict(list)
    for wp in wps:
        wp_by_deliverable[wp.deliverable_id].append(wp)
    deliverable_rows = []
    for d in sorted_deliverables:
        d_wps = wp_by_deliverable.get(d.id, [])
        deliverable_rows.append({
            "id": d.id,
            "title": d.title,
            "acceptance_met": 0,
            "acceptance_total": len(d.acceptance),
            "wp_done": sum(1 for w in d_wps if w.state == WPState.DONE),
            "wp_total": len(d_wps),
            "state": d.state,
        })

    workstream_rows = []
    for ws in workstreams:
        ws_wps = [wp for wp in wps if wp.workstream == ws.id]
        next_up = next((wp for wp in ws_wps if wp.state == WPState.DEFINED), None)
        workstream_rows.append({
            "id": ws.id,
            "owner": ws.owner_persona,
            "done": sum(1 for w in ws_wps if w.state == WPState.DONE),
            "in_review": sum(1 for w in ws_wps if w.state == WPState.IN_REVIEW),
            "blocked": sum(1 for w in ws_wps if w.state == WPState.BLOCKED),
            "next_up": next_up.id if next_up else None,
        })

    persona_activity: list[dict] = []
    daily: list[dict] = []
    blockers = [{
        "wp_id": wp.id, "workstream": wp.workstream,
        "reason": (wp.history[-1].event if wp.history else "unknown"),
        "needs_user": True, "suggestion": "investigate via audit log",
    } for wp in wps if wp.state == WPState.BLOCKED]
    decisions = _recent_decisions(project_root)
    up_next = [{
        "id": wp.id, "title": wp.title, "workstream": wp.workstream,
        "tier": int(wp.tier.value), "autonomy": wp.autonomy.value,
    } for wp in sorted(wps, key=lambda w: w.id) if wp.state == WPState.DEFINED][:10]

    pending_decisions = [{
        "wp_id": wp.id,
        "workstream": wp.workstream,
        "title": wp.title,
        "reason": (wp.history[-1].event if wp.history else "unknown"),
        "suggestion": "investigate via audit log",
    } for wp in wps if wp.state == WPState.BLOCKED]

    audit_rows = load_audit_index(project_root)
    if audit_rows:
        total_usd = sum(r.get("cost_usd", 0.0) for r in audit_rows)
        total_in = sum(r.get("tokens_in", 0) for r in audit_rows)
        total_out = sum(r.get("tokens_out", 0) for r in audit_rows)
        persona_map: dict[str, dict] = {}
        for r in audit_rows:
            p = r.get("persona", "unknown")
            if p not in persona_map:
                persona_map[p] = {"persona": p, "dispatches": 0,
                                  "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
            persona_map[p]["dispatches"] += 1
            persona_map[p]["tokens_in"] += r.get("tokens_in", 0)
            persona_map[p]["tokens_out"] += r.get("tokens_out", 0)
            persona_map[p]["cost_usd"] += r.get("cost_usd", 0.0)
        cost_burn = {
            "total_usd": total_usd,
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "by_persona": sorted(persona_map.values(), key=lambda x: x["persona"]),
        }
    else:
        cost_burn = {"total_usd": 0.0, "total_tokens_in": 0, "total_tokens_out": 0,
                     "by_persona": []}

    return dict(
        project=project,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sprint_number=_sprint_number(project_root),
        day_of_sprint=_day_of_sprint(project_root),
        deliverable_sequence=deliverable_sequence,
        deliverables_total=len(sorted_deliverables),
        deliverables_done=done_count,
        progress_pct=progress_pct,
        current_focus=current_focus,
        next_milestone=next_milestone,
        next_action=next_action,
        live=_live(project_root),
        health=health,
        deliverables=deliverable_rows,
        workstreams=workstream_rows,
        persona_activity=persona_activity,
        daily=daily,
        blockers=blockers,
        decisions=decisions,
        up_next=up_next,
        pending_decisions=pending_decisions,
        cost_burn=cost_burn,
    )


def render_dashboard(project_root: Path) -> Path:
    """Render the markdown dashboard. Returns its path."""
    ctx = _assemble_context(project_root)
    rendered = _template("dashboard.md.j2").render(**ctx)
    out_dir = state_dir(project_root) / "dashboards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "current.md"
    out.write_text(rendered, encoding="utf-8")
    return out


def render_dashboard_html(project_root: Path) -> Path:
    """Render the HTML dashboard. Returns its path."""
    ctx = _assemble_context(project_root)
    rendered = _template("dashboard.html.j2").render(**ctx)
    out_dir = state_dir(project_root) / "dashboards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "current.html"
    out.write_text(rendered, encoding="utf-8")
    return out


def render_dashboard_all(project_root: Path) -> dict[str, Path]:
    """Render markdown + HTML side by side. Returns a {format: path} map."""
    return {
        "md": render_dashboard(project_root),
        "html": render_dashboard_html(project_root),
    }
