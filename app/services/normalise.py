"""Normalisation of what a spider read: canonical URL, ISO date, reference number, clean title.

Pure functions, no I/O. The crawler's NormalisePipeline calls them; Detection (fingerprints,
CLAUDE.md rule 6) relies on the canonical URL and reference number they produce, so there is
one definition.
"""

import html
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# Parameters that change per visitor or campaign but never the document.
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "_ga", "ref_src", "igshid"}
_DEFAULT_PORTS = {"http": 80, "https": 443}
_SPACE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    """Unescape HTML entities, drop non-breaking spaces and collapse whitespace."""
    if not value:
        return ""
    return _SPACE.sub(" ", html.unescape(value).replace("\xa0", " ")).strip()


def canonical_url(url: str, base: str | None = None) -> str:
    """One spelling per document: lower-case scheme and host, no default port, no fragment, no
    tracking parameters, query sorted, no trailing slash (except the site root)."""
    url = clean_text(url)
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port in (None, _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    )
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


# Indian regulators write day first. Explicit `date_format` in a source's adapter_config wins.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b, %Y",
)


def parse_date(value: str | date | datetime | None, date_format: str | None = None) -> date | None:
    """The date as shown on the website, or None when it cannot be read. Never guesses."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    if date_format:  # strict: a configured format that does not match means "unreadable"
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    for parser in (datetime.fromisoformat, parsedate_to_datetime):  # ISO with time, RSS pubDate
        try:
            return parser(text).date()
        except (ValueError, TypeError):
            continue
    return None


# Reference numbers worth recognising in a title, most specific first. A source can add its own
# with `ref_no_regex` in its adapter_config.
REF_NO_PATTERNS = (
    r"\bRBI/\d{4}-\d{2,4}/\d+\b",  # RBI/2026-27/45
    # DOR.CRE.REC.No.12/03.10.001/2026-27
    r"\b[A-Za-z]{2,6}(?:\.[A-Za-z]{2,6}){1,5}\.(?:No\.)?\s?\d+[A-Za-z]?(?:/[\w.\-]+)+",
    r"\bFEMA\s*\d+\(R\)(?:/[\w\-]+)?",  # FEMA 23(R)/2026-RB
    r"\bCG-[A-Z]{2}-[A-Z]-\d{8}-\d+\b",  # CG-DL-E-12112026-266901
)


def normalise_ref_no(value: str | None) -> str | None:
    """Trimmed, whitespace collapsed, trailing punctuation removed; None when nothing is left."""
    text = clean_text(value).strip(" .,;:")
    return text or None


def find_ref_no(text: str | None, extra_pattern: str | None = None) -> str | None:
    """First reference number found in `text`, or None."""
    text = clean_text(text)
    patterns = ((extra_pattern,) if extra_pattern else ()) + REF_NO_PATTERNS
    for pattern in patterns:
        found = re.search(pattern, text)
        if found:
            return normalise_ref_no(found.group(1) if found.groups() else found.group(0))
    return None


# --- HTML to plain text ---------------------------------------------------------------------------

from html.parser import HTMLParser  # noqa: E402

_SKIP_TAGS = {"script", "style", "noscript", "template", "head"}
_BREAK_TAGS = {
    "p", "div", "br", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "blockquote", "pre",
}  # fmt: skip


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str | None) -> str:
    """Readable text of an HTML fragment or page: no tags, scripts or styles; lines kept."""
    if not markup:
        return ""
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    lines = (re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in "".join(parser.parts).splitlines())
    return "\n".join(ln for ln in lines if ln)
