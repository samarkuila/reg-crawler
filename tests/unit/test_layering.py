"""Architecture rules (repository-etiquette §4): UI → API → services → repositories."""

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
IMPORT = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.M)


def imports(path: pathlib.Path) -> set[str]:
    return set(IMPORT.findall(path.read_text(encoding="utf-8")))


def test_routers_never_import_repositories_or_the_db() -> None:
    for path in (APP / "api").glob("*.py"):
        bad = {m for m in imports(path) if m.startswith(("app.repositories", "app.db"))}
        # deps.py is the composition root: it picks the backend through the factory.
        allowed = {"app.repositories.factory"} if path.name == "deps.py" else set()
        assert bad <= allowed, (path.name, bad)
        assert not re.search(r"\bservices\.repo\b", path.read_text(encoding="utf-8")), path.name


def test_services_know_nothing_about_http() -> None:
    for path in (APP / "services").glob("*.py"):
        assert not {
            m for m in imports(path) if m.startswith(("fastapi", "starlette", "app.api"))
        }, path.name


def test_repositories_hold_no_business_layers() -> None:
    for path in (APP / "repositories").glob("*.py"):
        assert not {
            m for m in imports(path) if m.startswith(("app.services", "app.api", "fastapi"))
        }, path.name


def test_no_business_logic_in_javascript() -> None:
    # JS only calls /api/v1 and renders: no role checks, state names or decision rules.
    for path in (APP / "static" / "js").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "DECIDED" not in text and "approver" not in text.lower(), path.name
