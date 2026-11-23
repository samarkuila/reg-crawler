import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
STATIC = Path(__file__).resolve().parents[2] / "app" / "static"

COMPONENT_MARKERS = [
    'class="app-header"', 'class="nav"', 'class="card', "b-new", "b-mod", "b-back", "b-urg",
    "b-ok", "b-grey", "btn-p", "btn-g", "btn-o", "btn-d", "<table>", "dot-todo", "drawer",
    "toast", "modal", "empty", "skeleton", "pill",
]
EXTERNAL = re.compile(
    r"""(?:src|href)=["']https?://|url\(["']?https?://|@import\s+["']?https?:|from\s+["']https?://"""
)
NAV_LABELS = ["Daily review", "Source health", "Email preview", "Item history", "Sources",
              "Settings", "Audit log"]


def test_styleguide_renders_all_components() -> None:
    response = client.get("/styleguide")
    assert response.status_code == 200
    html = response.text
    for marker in COMPONENT_MARKERS:
        assert marker in html, marker
    for label in NAV_LABELS:
        assert f">{label}</a>" in html, label
    assert "Regulatory Monitor" in html and "POC" in html and "Run now" in html


def test_logo_is_placeholder_only() -> None:
    html = client.get("/styleguide").text
    assert "/static/img/logo-placeholder.svg" in html
    assert 'alt="Replace with official TVS Credit logo"' in html
    assert client.get("/static/img/logo-placeholder.svg").status_code == 200


def test_no_external_requests() -> None:
    sources = [client.get("/styleguide").text]
    for path in STATIC.rglob("*"):
        if path.suffix in {".css", ".js", ".svg", ".html"}:
            sources.append(path.read_text(encoding="utf-8"))
    for text in sources:
        # xmlns namespace URIs are identifiers, not requests.
        text = text.replace("http://www.w3.org/2000/svg", "")
        assert not EXTERNAL.search(text)


def test_fonts_self_hosted_with_licence() -> None:
    for weight in (400, 500, 600):
        assert (STATIC / "fonts" / f"jost-latin-{weight}.woff2").stat().st_size > 1000
    licence = (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licence.upper()
    css = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
    assert "fonts.googleapis" not in css and "fonts.gstatic" not in css


def test_static_assets_served() -> None:
    for path in ("css/tokens.css", "css/components.css", "js/ui.js", "js/api.js",
                 "fonts/jost-latin-400.woff2"):
        assert client.get(f"/static/{path}").status_code == 200, path
