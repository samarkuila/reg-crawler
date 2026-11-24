"""Command line tools. Run from the project folder:

    python -m app.cli import-sources config/sources.yaml --actor "Samarendra K."
    python -m app.cli import-monitoring-excel monitoring.xlsx --actor "Samarendra K."
    python -m app.cli seed-demo [--reset]
    python -m app.cli agent-eval --golden tests/fixtures/agent/golden.jsonl [--fake]
    python -m app.cli detect --run <run id> [--documents]
    python -m app.cli new-run --actor "Samarendra K." [--cycle ADHOC]

These always use the SQL database (DATABASE_URL, default data/reg_monitor.db). Imports only
propose changes: nothing becomes live until a different person approves it in the app.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from app.services.source_import import ImportError_, SourceImportService

ROOT = Path(__file__).resolve().parents[1]


def _repository():  # type: ignore[no-untyped-def]
    from app.repositories.factory import make_repository

    return make_repository("sql")


def cmd_import_sources(args: argparse.Namespace) -> int:
    service = SourceImportService(_repository())
    report = service.import_yaml(args.file, args.actor, dry_run=args.dry_run)
    print("\n".join(report.lines(args.dry_run)))
    return 0


def cmd_import_excel(args: argparse.Namespace) -> int:
    service = SourceImportService(_repository())
    report = service.import_excel(args.file, args.actor, dry_run=args.dry_run)
    print("\n".join(report.lines(args.dry_run)))
    return 0


def cmd_new_run(args: argparse.Namespace) -> int:
    """Create a crawl run (needed by `scrapy crawl ... -a run_id=<id>` when run by hand)."""
    from app.schemas.entities import now_ist
    from app.schemas.enums import Cycle

    run = _repository().create_run(Cycle(args.cycle), now_ist().date(), args.actor, args.actor)
    print(run.id)
    return 0


def cmd_rebaseline(args: argparse.Namespace) -> int:
    """Set a source back to NOT_BASELINED. Deletes nothing; writes an audit event."""
    from app.schemas.enums import BaselineStatus

    repo = _repository()
    source = repo.get_source(args.source)
    if source is None:
        raise ImportError_(f"Unknown source {args.source!r}.")
    if source.baseline_status == BaselineStatus.NOT_BASELINED:
        print(f"{source.id} is not baselined yet; nothing to do.")
        return 0
    repo.set_source_baseline(
        source.id,
        BaselineStatus.NOT_BASELINED,
        None,
        args.actor,
        "REBASELINE_REQUESTED",
        {"previous_window_start": str(source.baseline_window_start or ""), "via": "cli"},
    )
    print(f"{source.id}: its next crawl applies the first-run window again. Nothing was deleted.")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    """Turn the rows a crawl stored into items (and, with --documents, fetch their documents)."""
    from app.services.detection import DetectionService
    from app.services.documents import DocumentService, SubprocessDocumentFetcher

    repo = _repository()
    if repo.get_run(args.run) is None:
        raise ImportError_(f"Unknown run {args.run!r}. Create one with: new-run --actor <name>")
    result = DetectionService(repo).process(args.run)
    counts = {c: result.count(c) for c in ("NEW", "MODIFIED", "BACKDATED", "REAPPEARED")}
    print(f"Items: {counts} unchanged: {result.unchanged}")
    for warning in result.warnings:
        print(f"Warning ({warning.kind}): {warning.detail}")
    if args.documents:
        report = DocumentService(repo, SubprocessDocumentFetcher()).process(result.events)
        print(
            f"Documents: stored {report.stored}, blocked {report.blocked}, "
            f"failed {report.failed}, needs OCR {report.needs_ocr}, skipped {report.skipped}"
        )
        for note in report.notes:
            print(f"  - {note}")
    return 0


def cmd_agent_eval(args: argparse.Namespace) -> int:
    """Measure Luna on a labelled golden set and write docs/agent-eval-report.md."""
    from app.agents import evaluation
    from app.agents.luna.agent import LunaAgent
    from app.services.llm import LlmConfig, LoggedLlm, make_llm_client

    golden = Path(args.golden)
    items = evaluation.load_golden(golden)
    if args.fake:
        client, label = evaluation.heuristic_client(), "fake (keyword stand-in, no model)"
    else:
        config = LlmConfig.from_env()
        problem = config.problem()
        if problem:
            raise ImportError_(
                f"Cannot run the evaluation: {problem}. Set the AWS_GPT_LUNA_* variables in .env, "
                "or use --fake to check the plumbing only."
            )
        client, label = make_llm_client(config), f"AWS / {config.model}"
    text, version = "", None
    try:
        from app.agents import settings as agent_settings

        text, version = agent_settings.guidance(_repository())
    except RuntimeError:  # no database yet: the default guidance below is used
        pass
    if not text:
        from app.repositories.seed import SCREENING_GUIDANCE

        text = SCREENING_GUIDANCE
    report = evaluation.evaluate(LunaAgent(LoggedLlm(client)), items, text, version)
    report.client, report.fake = label, args.fake
    out = Path(args.out)
    note = (
        f"Only {len(items)} items: the spec asks for 50 or more labelled by Compliance. "
        "Treat the numbers as a smoke test."
        if len(items) < 50
        else ""
    )
    evaluation.write_report(report, out, golden, note)
    print(f"Wrote {out} ({len(items)} items, client: {label}).")
    for name, value in (
        ("recall", report.recall()),
        ("precision of HIGH", report.precision_high()),
        ("quote validity", report.quote_validity()),
        ("field accuracy", report.field_accuracy()),
    ):
        print(f"  {name}: {'n/a' if value is None else f'{value * 100:.0f}%'}")
    print(f"  cost: Rs {report.cost():.2f}")
    return 0


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from app.db.session import database_url, make_engine
    from app.repositories import seed
    from app.repositories.sql import SqlRepository

    url = database_url()
    parsed = make_url(url)
    if args.reset:
        if parsed.get_backend_name() != "sqlite" or not parsed.database:
            raise ImportError_("--reset only works with a SQLite file database.")
        for suffix in ("", "-wal", "-shm"):
            Path(parsed.database + suffix).unlink(missing_ok=True)
    _migrate(url)
    engine = make_engine(url)
    repo = SqlRepository(engine)
    if not repo.is_empty():
        engine.dispose()
        raise ImportError_(
            "The database already has data. Seed data only goes into an empty database. "
            "Use --reset to delete the SQLite file and start again."
        )
    try:
        seed.load_seed(repo)
    finally:
        engine.dispose()
    print(f"Demo data loaded into {url}.")
    print("Start the app with REPO_BACKEND=sql (see .env.example) to use it.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-sources", help="Propose sources from a sources.yaml file")
    p.add_argument("file")
    p.add_argument("--actor", required=True, help="Your name as shown in the app")
    p.add_argument("--dry-run", action="store_true", help="Show what would be proposed")
    p.set_defaults(func=cmd_import_sources)

    p = sub.add_parser("import-monitoring-excel", help="Propose sources and keywords from Excel")
    p.add_argument("file")
    p.add_argument("--actor", required=True, help="Your name as shown in the app")
    p.add_argument("--dry-run", action="store_true", help="Show what would be proposed")
    p.set_defaults(func=cmd_import_excel)

    p = sub.add_parser("new-run", help="Create a crawl run and print its id (manual crawls)")
    p.add_argument("--actor", required=True, help="Your name as shown in the app")
    p.add_argument("--cycle", default="ADHOC", choices=["MORNING", "EOD", "ADHOC", "URGENT"])
    p.set_defaults(func=cmd_new_run)

    p = sub.add_parser("rebaseline", help="Apply the first-run window again on the next crawl")
    p.add_argument("--source", required=True, help="Source id")
    p.add_argument("--actor", required=True, help="Your name as shown in the app")
    p.set_defaults(func=cmd_rebaseline)

    p = sub.add_parser("detect", help="Detect items from a crawl run's stored rows")
    p.add_argument("--run", required=True, help="Crawl run id (see new-run)")
    p.add_argument("--documents", action="store_true", help="Also fetch and store documents")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("agent-eval", help="Evaluate Luna on a golden set (writes a report)")
    p.add_argument("--golden", default="tests/fixtures/agent/golden.jsonl")
    p.add_argument("--out", default="docs/agent-eval-report.md")
    p.add_argument("--fake", action="store_true", help="Keyword stand-in: checks plumbing only")
    p.set_defaults(func=cmd_agent_eval)

    p = sub.add_parser("seed-demo", help="Load the mockup demo data into an empty database")
    p.add_argument("--reset", action="store_true", help="Delete the SQLite file first")
    p.set_defaults(func=cmd_seed_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ImportError_, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
