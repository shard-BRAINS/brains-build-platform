"""Tests for render_dashboard.py."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from build_platform.audit import AuditEntry, write_audit
from build_platform.paths import state_dir
from build_platform.render_dashboard import (
    render_dashboard,
    render_dashboard_all,
    render_dashboard_html,
)
from build_platform.schemas import (
    Autonomy,
    Config,
    Deliverable,
    OllamaConfig,
    OllamaModels,
    Project,
    ProjectConfig,
    WorkPackage,
    Workstream,
    WPState,
    WPTier,
)
from build_platform.state import (
    append_work_package,
    init_state_tree,
    save_config,
    save_deliverables,
    save_project,
    save_workstreams,
    update_wp_state,
)


def _seed(tmp_path: Path) -> Path:
    init_state_tree(tmp_path)
    save_project(tmp_path, Project(
        name="Demo", mission="Demonstrate", stack=["python"], constraints=[],
        ground_truth="local", created="2026-05-25T10:00:00Z",
    ))
    save_deliverables(tmp_path, [
        Deliverable(id="D-auth", title="Auth", why="users", acceptance=["login works"],
                    sequence=1, state="in_progress"),
        Deliverable(id="D-ui", title="UI", why="users", acceptance=["page renders"],
                    sequence=2, state="not_started"),
    ])
    save_workstreams(tmp_path, [
        Workstream(id="backend", owner_persona="build-backend-sme",
                   review_persona="build-dev-orchestrator", description="Server"),
    ])
    save_config(tmp_path, Config(
        ollama=OllamaConfig(models=OllamaModels()),
        project=ProjectConfig(test_command="pytest"),
    ))
    append_work_package(tmp_path, WorkPackage(
        id="WP-0001", title="Login endpoint", workstream="backend", deliverable_id="D-auth",
        tier=WPTier.TWO, executor_persona="build-backend-sme", spec="implement login",
        spec_files=["src/auth/login.py"], acceptance=["test passes"], depends_on=[], consult=[],
        state=WPState.DONE, created_by="build-dev-orchestrator",
        created_at="2026-05-24T10:00:00Z", history=[],
    ))
    append_work_package(tmp_path, WorkPackage(
        id="WP-0002", title="Session refresh", workstream="backend", deliverable_id="D-auth",
        tier=WPTier.TWO, executor_persona="build-backend-sme", spec="refresh sessions",
        spec_files=["src/auth/session.py"], acceptance=["test passes"], depends_on=[], consult=[],
        state=WPState.DEFINED, created_by="build-dev-orchestrator",
        created_at="2026-05-25T10:00:00Z", history=[],
    ))
    return tmp_path


def test_render_dashboard_writes_file(tmp_path: Path):
    _seed(tmp_path)
    out = render_dashboard(tmp_path)
    assert out.exists()
    assert out.name == "current.md"
    assert out.parent.name == "dashboards"


def test_render_dashboard_includes_required_sections(tmp_path: Path):
    _seed(tmp_path)
    out = render_dashboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    for section in [
        "# Demo — PMO Dashboard",
        "## Plan position",
        "## Live (right now)",
        "## Health",
        "## Deliverables",
        "## Workstreams",
        "## Persona activity",
        "## Daily completed work",
        "## Open blockers",
        "## Recent decisions",
        "## Up next",
    ]:
        assert section in text, f"Missing section: {section}"


def test_render_dashboard_lists_open_wp(tmp_path: Path):
    _seed(tmp_path)
    out = render_dashboard(tmp_path)
    assert "WP-0002" in out.read_text(encoding="utf-8")


def test_render_dashboard_empty_sections_render_as_none(tmp_path: Path):
    init_state_tree(tmp_path)
    save_project(tmp_path, Project(
        name="Empty", mission="x", stack=[], constraints=[],
        ground_truth="local", created="2026-05-25T10:00:00Z",
    ))
    save_deliverables(tmp_path, [])
    save_workstreams(tmp_path, [])
    save_config(tmp_path, Config(
        ollama=OllamaConfig(models=OllamaModels()),
        project=ProjectConfig(test_command="pytest"),
    ))
    out = render_dashboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "_None_" in text


def test_render_dashboard_html_writes_file(tmp_path: Path):
    _seed(tmp_path)
    out = render_dashboard_html(tmp_path)
    assert out.exists()
    assert out.name == "current.html"
    assert out.parent.name == "dashboards"


def test_render_dashboard_html_is_valid_html(tmp_path: Path):
    _seed(tmp_path)
    out = render_dashboard_html(tmp_path)
    html = out.read_text(encoding="utf-8")
    assert html.lstrip().startswith("<!doctype html>")
    assert "<title>Demo — PMO Dashboard</title>" in html
    assert "</html>" in html


def test_render_dashboard_html_uses_brand_colors(tmp_path: Path):
    """Brand: Gold Deep #D99518 on white. No raw #FCC14D body usage."""
    _seed(tmp_path)
    html = render_dashboard_html(tmp_path).read_text(encoding="utf-8")
    assert "#D99518" in html  # Gold Deep token defined
    # FCC14D is allowed as a token but only as decorative (--gold), never as body color
    # The template defines it under --gold then uses --gold-deep for text
    assert "--gold-deep" in html
    assert "Atkinson Hyperlegible" in html  # accessibility-first font
    assert "text-align: left" in html  # no justified body per brand


def test_render_dashboard_html_includes_all_sections(tmp_path: Path):
    _seed(tmp_path)
    html = render_dashboard_html(tmp_path).read_text(encoding="utf-8")
    for section in [
        "Plan position", "Live (right now)", "Health", "Deliverables",
        "Workstreams", "Persona activity", "Daily completed work",
        "Open blockers", "Recent decisions", "Up next",
    ]:
        assert section in html, f"Missing section: {section}"


def test_render_dashboard_html_lists_open_wp(tmp_path: Path):
    _seed(tmp_path)
    html = render_dashboard_html(tmp_path).read_text(encoding="utf-8")
    assert "WP-0002" in html


def test_render_dashboard_all_writes_both(tmp_path: Path):
    _seed(tmp_path)
    paths = render_dashboard_all(tmp_path)
    assert "md" in paths and "html" in paths
    assert paths["md"].exists()
    assert paths["html"].exists()
    assert paths["md"].name == "current.md"
    assert paths["html"].name == "current.html"


# --- Autonomy column, pending decisions, cost burn ---

def _seed_extended(tmp_path: Path) -> Path:
    """Extend _seed with an auto WP, a blocked WP, and audit entries."""
    _seed(tmp_path)
    append_work_package(tmp_path, WorkPackage(
        id="WP-0003", title="Auto tier1 job", workstream="backend",
        deliverable_id="D-auth", tier=WPTier.ONE,
        executor_persona="build-backend-sme", spec="auto spec",
        spec_files=["src/auto.py"], acceptance=["ok"], depends_on=[], consult=[],
        state=WPState.DEFINED, created_by="build-dev-orchestrator",
        created_at="2026-05-25T11:00:00Z", history=[],
        autonomy=Autonomy.AUTO,
    ))
    return tmp_path


def test_dashboard_md_includes_autonomy_per_up_next(tmp_path: Path):
    _seed_extended(tmp_path)
    out = render_dashboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    # WP-0002 has default autonomy=manual, WP-0003 has auto
    assert "manual" in text
    assert "auto" in text


def test_dashboard_md_includes_pending_decisions_section(tmp_path: Path):
    _seed(tmp_path)
    out = render_dashboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "## Pending decisions" in text


def test_dashboard_md_pending_decisions_lists_blocked_wp(tmp_path: Path):
    _seed(tmp_path)
    update_wp_state(tmp_path, "WP-0002", WPState.BLOCKED,
                    by="build-dev-orchestrator", event="blocked for test")
    out = render_dashboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "WP-0002" in text
    assert "blocked for test" in text


def test_dashboard_md_includes_cost_burn_section(tmp_path: Path):
    _seed(tmp_path)
    out = render_dashboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "## Cost burn" in text


def test_dashboard_md_cost_burn_rolls_up_audit_index(tmp_path: Path):
    _seed(tmp_path)
    write_audit(tmp_path, AuditEntry(
        wp_id="WP-0001", timestamp="2026-05-25T14:00:00Z",
        persona="build-backend-sme", model="claude-sonnet-4-6",
        tier=2, runtime_seconds=10.0, result="done",
        tokens_in=1000, tokens_out=500, cost_usd=0.003,
    ))
    write_audit(tmp_path, AuditEntry(
        wp_id="WP-0002", timestamp="2026-05-25T15:00:00Z",
        persona="build-qa-sme", model="claude-sonnet-4-6",
        tier=2, runtime_seconds=8.0, result="done",
        tokens_in=800, tokens_out=300, cost_usd=0.002,
    ))
    out = render_dashboard(tmp_path)
    text = out.read_text(encoding="utf-8")
    # Total cost = 0.003 + 0.002 = 0.005
    assert "0.0050" in text
    # Total tokens_in = 1800
    assert "1800" in text
    # Per-persona row
    assert "build-backend-sme" in text
    assert "build-qa-sme" in text


def test_dashboard_html_includes_cost_burn(tmp_path: Path):
    _seed(tmp_path)
    write_audit(tmp_path, AuditEntry(
        wp_id="WP-0001", timestamp="2026-05-25T14:00:00Z",
        persona="build-backend-sme", model="claude-sonnet-4-6",
        tier=2, runtime_seconds=10.0, result="done",
        tokens_in=500, tokens_out=250, cost_usd=0.001,
    ))
    html = render_dashboard_html(tmp_path).read_text(encoding="utf-8")
    assert "Cost burn" in html
    assert "0.0010" in html


# --- Recent decisions (last 7 days) --------------------------------------


def _write_decision(tmp_path: Path, *, day: date, title: str,
                    owner: str = "build-dev-orchestrator", related: str = "WP-0001") -> None:
    """Append a decision to the ledger in the exact shape `/build-decision` writes."""
    ledger = state_dir(tmp_path) / "decisions.md"
    ledger.write_text(
        ledger.read_text(encoding="utf-8") if ledger.exists() else "",
        encoding="utf-8",
    )
    with ledger.open("a", encoding="utf-8") as f:
        f.write(
            f"\n## {day.isoformat()} — {title}\n"
            f"**Owner:** {owner}\n"
            f"**Decision:** something\n"
            f"**Why:** because\n"
            f"**Alternatives considered:** _None_\n"
            f"**Related WPs:** {related}\n"
            f"**Audit:** _None_\n"
        )


def test_dashboard_lists_recent_decision(tmp_path: Path):
    _seed(tmp_path)
    today = datetime.now(timezone.utc).date()
    _write_decision(tmp_path, day=today, title="Adopt hexagonal layout",
                    owner="user:matthew", related="WP-0002")
    text = render_dashboard(tmp_path).read_text(encoding="utf-8")
    assert "Adopt hexagonal layout" in text
    assert "user:matthew" in text
    assert "WP-0002" in text


def test_dashboard_omits_decisions_older_than_7_days(tmp_path: Path):
    _seed(tmp_path)
    today = datetime.now(timezone.utc).date()
    _write_decision(tmp_path, day=today - timedelta(days=40), title="Stale decision")
    _write_decision(tmp_path, day=today - timedelta(days=1), title="Fresh decision")
    text = render_dashboard(tmp_path).read_text(encoding="utf-8")
    assert "Fresh decision" in text
    assert "Stale decision" not in text


def test_dashboard_decisions_empty_without_ledger(tmp_path: Path):
    _seed(tmp_path)
    # No decisions.md written — section header still renders, but no entries.
    text = render_dashboard(tmp_path).read_text(encoding="utf-8")
    assert "## Recent decisions" in text
