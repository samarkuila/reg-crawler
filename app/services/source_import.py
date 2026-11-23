"""Import sources from config/sources.yaml and from Compliance's website monitoring Excel.

Every import only PROPOSES: a new source starts inactive with a PENDING version 1, a change to an
existing source becomes a PENDING version, a keyword change becomes a pending setting. Nothing is
live until a different person approves it (CLAUDE.md rule 9). Importing the same file twice
proposes nothing new, and a source that already has a change waiting is left alone and reported.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.repositories.base import Repository
from app.schemas.entities import Source, SourceVersion, now_ist
from app.schemas.enums import Priority, SourceType, VersionStatus

# Fields a source version can carry. `planned` is set once, when the source is registered.
FIELDS = (
    "authority",
    "page_name",
    "type",
    "url",
    "rss_url",
    "adapter_config",
    "include_keywords",
    "exclude_keywords",
    "priority",
    "requires_js",
    "active",
)
GLOBAL_SITES = {"all", "global", "general", "common", "all sites"}
_URL = re.compile(r"^https?://\S+$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")

HEADERS = {
    "site": {
        "site",
        "website",
        "authority",
        "regulator",
        "source",
        "name of site",
        "name",
        "site name",
        "website name",
    },
    "link": {
        "link",
        "url",
        "website link",
        "web link",
        "site link",
        "website url",
        "page url",
        "weblink",
    },
    "tabs": {
        "tab",
        "tabs",
        "page",
        "pages",
        "section",
        "sections",
        "page name",
        "tabs keywords",
        "tabs and keywords",
    },
    "keywords": {"keyword", "keywords", "search keywords", "key words", "include keywords"},
    "exclude": {
        "exclude",
        "exclude keywords",
        "exclusion keywords",
        "exclusions",
        "negative keywords",
    },
    "documents": {
        "documents to fetch",
        "document to fetch",
        "documents",
        "docs to fetch",
        "documents to download",
        "download",
    },
}


class ImportError_(Exception):  # noqa: N818 - named for the CLI message, not a Python builtin clash
    """The file cannot be read at all (not a row-level problem)."""


@dataclass
class ImportReport:
    proposed_new: list[str] = field(default_factory=list)
    proposed_changes: list[str] = field(default_factory=list)
    proposed_settings: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # (reason included) left alone
    unmapped: list[str] = field(default_factory=list)  # rows that could not be used (reason)

    def lines(self, dry_run: bool = False) -> list[str]:
        verb = "Would propose" if dry_run else "Proposed"
        out = [
            f"{verb}: {len(self.proposed_new)} new source(s), {len(self.proposed_changes)} "
            f"change(s) to existing sources, {len(self.proposed_settings)} keyword setting(s).",
            f"Unchanged: {len(self.unchanged)}. Left alone: {len(self.skipped)}. "
            f"Rows not mapped: {len(self.unmapped)}.",
        ]
        for title, items in (
            ("New sources", self.proposed_new),
            ("Changes", self.proposed_changes),
            ("Keyword settings", self.proposed_settings),
            ("Left alone", self.skipped),
            ("Rows that could not be mapped", self.unmapped),
        ):
            if items:
                out.append(f"{title}:")
                out.extend(f"  - {i}" for i in items)
        if not dry_run and (self.proposed_new or self.proposed_changes or self.proposed_settings):
            out.append("Everything above is pending approval. Open /sources or /settings.")
        return out


class SourceSpec(BaseModel):
    """One entry of sources.yaml (docs/architecture.md section 6)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    authority: str = Field(min_length=1)
    page_name: str = Field(min_length=1)
    type: SourceType
    url: str
    rss_url: str | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    priority: Priority = Priority.NORMAL
    requires_js: bool = False
    active: bool = True
    planned: bool = False

    @model_validator(mode="after")
    def _check(self) -> "SourceSpec":
        if not _SLUG.match(self.id):
            raise ValueError(f"id '{self.id}' must be lower-case letters, digits and dashes")
        if self.planned and self.active:
            raise ValueError("a planned source must have active: false")
        if not (_URL.match(self.url) or (self.planned and self.url == "TBD")):
            raise ValueError("url must start with http:// or https:// (TBD only when planned)")
        if self.rss_url and not _URL.match(self.rss_url):
            raise ValueError("rss_url must start with http:// or https://")
        return self


def _clean(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def _merge(existing: list[str], extra: list[str]) -> list[str]:
    return _clean([*existing, *extra])


def _norm_header(text: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()


def _split(text: Any) -> list[str]:
    return _clean(re.split(r"[;,\n]", str(text))) if text not in (None, "") else []


def _host(url: str | None) -> str:
    host = urlparse(url or "").netloc.lower()
    return host.removeprefix("www.")


class SourceImportService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    # --- shared ------------------------------------------------------------------------------

    def check_actor(self, actor: str) -> str:
        """The importing person must be a known user (when users exist), so audit names are real."""
        users = self._repo.list_users()
        if not users:
            return actor.strip()
        for u in users:
            if u.name.lower() == actor.strip().lower():
                return u.name
        names = ", ".join(u.name for u in users)
        raise ImportError_(f"Unknown actor '{actor}'. Use one of: {names}")

    def _effective(self, source: Source) -> dict[str, Any]:
        """The source as it will be once any waiting change is approved."""
        fields = source.model_dump(mode="json")
        pending = [
            v
            for v in self._repo.list_source_versions(source.id)
            if v.status == VersionStatus.PENDING
        ]
        if pending:
            fields.update(pending[-1].after)
        return fields

    def _has_pending(self, source_id: str) -> bool:
        return any(
            v.status == VersionStatus.PENDING for v in self._repo.list_source_versions(source_id)
        )

    def _propose_new(self, spec: SourceSpec, actor: str, write: bool) -> None:
        if not write:
            return
        fields = spec.model_dump(mode="json", exclude={"id", "planned"})
        source = Source(
            id=spec.id,
            planned=spec.planned,
            current_version=0,
            **{
                **spec.model_dump(exclude={"id", "planned", "active"}),
                "adapter_config": fields["adapter_config"],
            },
            active=False,
        )
        version = SourceVersion(
            id=f"srcv-{spec.id}-1",
            source_id=spec.id,
            version=1,
            before={},
            after={**fields, "active": spec.active},
            status=VersionStatus.PENDING,
            requested_by=actor,
            ts=now_ist(),
        )
        self._repo.create_source(source, version, actor)

    # --- config/sources.yaml -------------------------------------------------------------------

    def parse_yaml(self, text: str) -> list[SourceSpec]:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ImportError_(f"The file is not valid YAML: {exc}") from exc
        if not isinstance(data, list):
            raise ImportError_("sources.yaml must be a list of sources.")
        specs: list[SourceSpec] = []
        problems: list[str] = []
        for n, entry in enumerate(data, start=1):
            try:
                specs.append(SourceSpec.model_validate(entry))
            except ValidationError as exc:
                name = entry.get("id", f"entry {n}") if isinstance(entry, dict) else f"entry {n}"
                reason = "; ".join(e["msg"] for e in exc.errors())
                problems.append(f"{name}: {reason}")
        if problems:
            raise ImportError_("sources.yaml has errors:\n  - " + "\n  - ".join(problems))
        ids = [s.id for s in specs]
        if len(set(ids)) != len(ids):
            raise ImportError_("sources.yaml lists the same id twice.")
        return specs

    def import_yaml(self, path: str | Path, actor: str, dry_run: bool = False) -> ImportReport:
        actor = self.check_actor(actor)
        file = Path(path)
        if not file.is_file():
            raise ImportError_(f"File not found: {file}")
        specs = self.parse_yaml(file.read_text(encoding="utf-8"))
        report = ImportReport()
        for spec in specs:
            label = f"{spec.authority} · {spec.page_name} ({spec.id})"
            existing = self._repo.get_source(spec.id)
            if existing is None:
                self._propose_new(spec, actor, write=not dry_run)
                report.proposed_new.append(label + (" [planned]" if spec.planned else ""))
                continue
            wanted = spec.model_dump(mode="json")
            current = self._effective(existing)
            diff = {k: wanted[k] for k in FIELDS if current.get(k) != wanted[k]}
            if not diff:
                report.unchanged.append(label)
            elif self._has_pending(spec.id):
                report.skipped.append(
                    f"{label}: a change is already waiting for approval; approve or reject it, "
                    "then import again"
                )
            else:
                if not dry_run:
                    self._repo.add_source_version(spec.id, diff, actor)
                report.proposed_changes.append(f"{label}: " + ", ".join(sorted(diff)))
        return report

    # --- Compliance's monitoring Excel -------------------------------------------------------

    def _read_rows(self, path: Path) -> list[tuple[str, int, dict[str, Any]]]:
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - any unreadable file gets one clear message
            raise ImportError_(f"Could not open the Excel file: {exc}") from exc
        rows: list[tuple[str, int, dict[str, Any]]] = []
        for sheet in workbook.worksheets:
            header: dict[int, str] | None = None
            for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                if header is None:
                    found = {
                        i: key
                        for i, cell in enumerate(values)
                        for key, names in HEADERS.items()
                        if _norm_header(cell) in names
                    }
                    if len(set(found.values())) >= 2 and "site" in found.values():
                        header = found
                    elif number > 15:
                        break
                    continue
                if not any(c not in (None, "") for c in values):
                    continue
                row: dict[str, Any] = {}
                for i, key in header.items():
                    if i < len(values) and values[i] not in (None, ""):
                        row[key] = str(values[i]).strip() if key != "documents" else str(values[i])
                rows.append((sheet.title, number, row))
        workbook.close()
        return rows

    def _match(self, sources: list[Source], site: str, link: str | None, tabs: str | None):  # type: ignore[no-untyped-def]
        key = site.casefold().strip()
        host = _host(link)
        found = [
            s
            for s in sources
            if s.authority.casefold() == key or (host and host in {_host(s.url), _host(s.rss_url)})
        ]
        if len(found) > 1 and tabs:
            by_tab = [s for s in found if s.page_name.casefold() == tabs.casefold().strip()]
            found = by_tab or found
        if len(found) > 1 and link:
            same = [s for s in found if s.url.rstrip("/") == link.rstrip("/")]
            found = same or found
        return found

    def import_excel(self, path: str | Path, actor: str, dry_run: bool = False) -> ImportReport:
        actor = self.check_actor(actor)
        file = Path(path)
        if not file.is_file():
            raise ImportError_(f"File not found: {file}")
        rows = self._read_rows(file)
        if not rows:
            raise ImportError_(
                "No data rows found. The sheet needs a header row with at least 'Site' and "
                "'Link' (or 'Website' and 'URL') columns."
            )
        report = ImportReport()
        sources = self._repo.list_sources()
        wanted: dict[str, dict[str, Any]] = {}  # source id -> merged proposal from all its rows
        new_specs: dict[str, SourceSpec] = {}
        global_include: list[str] = []
        global_exclude: list[str] = []

        for sheet, number, row in rows:
            where = f"{sheet} row {number}"
            site = row.get("site", "")
            if not site:
                report.unmapped.append(f"{where}: no site name")
                continue
            keywords, exclude = _split(row.get("keywords")), _split(row.get("exclude"))
            if site.casefold() in GLOBAL_SITES:
                global_include += keywords
                global_exclude += exclude
                continue
            link = row.get("link")
            if link and not re.match(r"^https?://", link) and "." in link and " " not in link:
                link = "https://" + link
            if not link or not _URL.match(link):
                report.unmapped.append(f"{where} ({site}): no usable web address in 'Link'")
                continue
            tabs = row.get("tabs")
            documents = _split(row.get("documents"))
            matches = self._match(sources, site, link, tabs)
            if len(matches) > 1:
                names = ", ".join(s.id for s in matches)
                report.unmapped.append(f"{where} ({site}): matches several sources ({names})")
                continue
            if not matches:
                slug = re.sub(r"[^a-z0-9]+", "-", f"{site}-{tabs or 'imported'}".lower()).strip(
                    "-"
                )[:60]
                if slug in new_specs:
                    spec = new_specs[slug]
                    spec.include_keywords = _merge(spec.include_keywords, keywords)
                    spec.exclude_keywords = _merge(spec.exclude_keywords, exclude)
                    continue
                try:
                    new_specs[slug] = SourceSpec(
                        id=slug if len(slug) >= 2 else f"{slug}-src",
                        authority=site,
                        page_name=tabs or "Imported from monitoring Excel",
                        type=SourceType.HTML_LIST,
                        url=link,
                        adapter_config={
                            **({"tabs": _split(tabs)} if tabs else {}),
                            **({"documents_to_fetch": documents} if documents else {}),
                            "selectors_confirmed": False,
                        },
                        include_keywords=_clean(keywords),
                        exclude_keywords=_clean(exclude),
                    )
                except ValidationError as exc:
                    report.unmapped.append(
                        f"{where} ({site}): {'; '.join(e['msg'] for e in exc.errors())}"
                    )
                continue
            source = matches[0]
            entry = wanted.setdefault(
                source.id, {"source": source, "keywords": [], "exclude": [], "tabs": [], "docs": []}
            )
            entry["keywords"] += keywords
            entry["exclude"] += exclude
            entry["tabs"] += _split(tabs)
            entry["docs"] += documents

        for source_id, entry in wanted.items():
            source: Source = entry["source"]
            label = f"{source.authority} · {source.page_name} ({source_id})"
            current = self._effective(source)
            config = dict(current.get("adapter_config") or {})
            if entry["tabs"]:
                config["tabs"] = _merge(config.get("tabs", []), entry["tabs"])
            if entry["docs"]:
                config["documents_to_fetch"] = _merge(
                    config.get("documents_to_fetch", []), entry["docs"]
                )
            diff: dict[str, Any] = {}
            include = _merge(current.get("include_keywords", []), entry["keywords"])
            exclude = _merge(current.get("exclude_keywords", []), entry["exclude"])
            if include != current.get("include_keywords"):
                diff["include_keywords"] = include
            if exclude != current.get("exclude_keywords"):
                diff["exclude_keywords"] = exclude
            if config != (current.get("adapter_config") or {}):
                diff["adapter_config"] = config
            if not diff:
                report.unchanged.append(label)
            elif self._has_pending(source_id):
                report.skipped.append(f"{label}: a change is already waiting for approval")
            else:
                if not dry_run:
                    self._repo.add_source_version(source_id, diff, actor)
                report.proposed_changes.append(f"{label}: " + ", ".join(sorted(diff)))

        for spec in new_specs.values():
            label = f"{spec.authority} · {spec.page_name} ({spec.id})"
            if self._repo.get_source(spec.id) is not None:
                report.skipped.append(
                    f"{label}: a source with this id exists but did not match the row"
                )
                continue
            self._propose_new(spec, actor, write=not dry_run)
            report.proposed_new.append(label)

        for key, extra, title in (
            ("relevance-keywords", global_include, "Relevance keywords"),
            ("exclusion-keywords", global_exclude, "Exclusion keywords"),
        ):
            setting = self._repo.get_setting(key)
            if not extra or setting is None:
                continue
            base = setting.pending.value if setting.pending else setting.value
            merged = _merge(base, extra)
            if merged == base:
                report.unchanged.append(f"{title} (setting)")
            elif setting.pending is not None:
                report.skipped.append(
                    f"{title} (setting): a change is already waiting for approval"
                )
            else:
                if not dry_run:
                    self._repo.propose_setting(key, merged, actor, actor)
                report.proposed_settings.append(
                    f"{title}: +{len(merged) - len(setting.value)} keyword(s)"
                )
        return report
