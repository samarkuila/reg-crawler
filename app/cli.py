"""Command line tools. Run from the project folder:

    python -m app.cli import-sources config/sources.yaml --actor "Samarendra K."
    python -m app.cli import-monitoring-excel monitoring.xlsx --actor "Samarendra K."
    python -m app.cli seed-demo [--reset]

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
