"""Config service: sources and settings as versioned data with maker–checker approval.

A change never applies directly: it becomes a PENDING version that a *different* approver must
approve (CLAUDE.md rule 9). Unapproved versions are never used by crawls.
"""

from datetime import date
from typing import Any

from app.repositories.base import Repository
from app.schemas.api import (
    SettingView,
    SmeFunctionOption,
    SourceChanges,
    SourceCreate,
    SourceTestResult,
    SourceTestRow,
    SourceView,
)
from app.schemas.entities import AppUser, Source, SourceVersion, now_ist
from app.schemas.enums import CheckStatus, SourceType, VersionStatus
from app.services.errors import ConflictError, InvalidError, NotFoundError
from app.services.permissions import (
    APPROVER_ROLES,
    CONFIG_ROLES,
    require_different_person,
    require_role,
)

_HOW = {
    SourceType.RSS: "RSS feed + HTML listing",
    SourceType.HTML_LIST: "HTML listing",
    SourceType.STRUCTURED_DATA: "Official data file",
    SourceType.BROWSER: "Browser-rendered page",
}


class ConfigService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def list_sme_functions(self) -> list[SmeFunctionOption]:
        """Active SME functions for the review form's dropdown (id + name only)."""
        return [
            SmeFunctionOption(id=f.id, name=f.name)
            for f in self._repo.list_sme_functions()
            if f.active
        ]

    # --- sources -----------------------------------------------------------------------------

    def _source(self, source_id: str) -> Source:
        source = self._repo.get_source(source_id)
        if source is None:
            raise NotFoundError(f"Source {source_id} not found")
        return source

    def _view(self, source: Source) -> SourceView:
        pending = [
            v
            for v in self._repo.list_source_versions(source.id)
            if v.status == VersionStatus.PENDING
        ]
        if source.active:
            label = "Active"
        elif source.planned:
            label = "Planned (V2)"
        elif source.current_version == 0:
            label = "Waiting for approval"
        else:
            label = "Inactive"
        return SourceView(
            source=source,
            how_we_read_it=_HOW[source.type],
            status_label=label,
            pending_version=pending[-1] if pending else None,
        )

    def list_sources(self) -> list[SourceView]:
        sources = sorted(
            self._repo.list_sources(), key=lambda s: (not s.active, s.planned, s.authority.lower())
        )
        return [self._view(s) for s in sources]

    def create_source(self, user: AppUser, body: SourceCreate) -> SourceView:
        require_role(user, CONFIG_ROLES, "add a source")
        if self._repo.get_source(body.id) is not None:
            raise ConflictError(f"A source with id '{body.id}' already exists.")
        fields = body.model_dump(mode="json", exclude={"id"})
        source = Source(
            id=body.id, active=False, current_version=0, **body.model_dump(exclude={"id"})
        )
        version = SourceVersion(
            id=f"srcv-{body.id}-1",
            source_id=body.id,
            version=1,
            before={},
            after={**fields, "active": True},
            status=VersionStatus.PENDING,
            requested_by=user.name,
            ts=now_ist(),
        )
        return self._view(self._repo.create_source(source, version, user.name))

    def list_versions(self, source_id: str) -> list[SourceVersion]:
        self._source(source_id)
        return self._repo.list_source_versions(source_id)

    def propose_version(
        self, user: AppUser, source_id: str, changes: SourceChanges
    ) -> SourceVersion:
        require_role(user, CONFIG_ROLES, "change a source")
        source = self._source(source_id)
        after = changes.model_dump(mode="json", exclude_unset=True)
        current = source.model_dump(mode="json")
        after = {k: v for k, v in after.items() if current.get(k) != v}
        if not after:
            raise InvalidError("Nothing would change. Edit at least one field.")
        return self._repo.add_source_version(source_id, after, user.name)

    def _pending(self, source_id: str, version: int) -> SourceVersion:
        self._source(source_id)
        ver = self._repo.get_source_version(source_id, version)
        if ver is None:
            raise NotFoundError(f"Version {version} of {source_id} not found")
        if ver.status != VersionStatus.PENDING:
            raise ConflictError(f"Version {version} is already {ver.status.value.lower()}.")
        return ver

    def approve_version(self, user: AppUser, source_id: str, version: int) -> SourceVersion:
        require_role(user, APPROVER_ROLES, "approve a source change")
        ver = self._pending(source_id, version)
        require_different_person(user, ver.requested_by, "approve this change")
        return self._repo.approve_source_version(source_id, version, user.name, user.name)

    def reject_version(
        self, user: AppUser, source_id: str, version: int, reason: str
    ) -> SourceVersion:
        require_role(user, APPROVER_ROLES, "reject a source change")
        ver = self._pending(source_id, version)
        require_different_person(user, ver.requested_by, "reject this change")
        if not reason.strip():
            raise InvalidError("A reason is required to reject a change.")
        return self._repo.reject_source_version(
            source_id, version, user.name, reason.strip(), user.name
        )

    def test_source(self, user: AppUser, source_id: str) -> SourceTestResult:
        """Mock dry run. The real one (Prompt 9+) fetches politely; it never saves items."""
        require_role(user, CONFIG_ROLES, "test a source")
        source = self._source(source_id)
        if source.id == "egazette":
            return SourceTestResult(
                source_id=source.id,
                status=CheckStatus.BLOCKED,
                duration_seconds=1.4,
                rows=[],
                message="The site showed a verification page. The test stopped; nothing was saved.",
            )
        rows = [
            SourceTestRow(
                title=f"{source.authority} sample row {n}",
                link=f"{source.url.rstrip('/')}/sample-{n}",
                displayed_date=date(2026, 11, 20),
                ref_no=None,
            )
            for n in (1, 2, 3)
        ]
        return SourceTestResult(
            source_id=source.id,
            status=CheckStatus.OK,
            duration_seconds=1.2,
            rows=rows,
            message="Mock result for the POC build. Real parsing arrives with the crawler.",
        )

    # --- settings ----------------------------------------------------------------------------

    def list_settings(self) -> list[SettingView]:
        return [
            SettingView(setting=s, has_pending=s.pending is not None)
            for s in self._repo.list_settings()
        ]

    def get_setting(self, key: str) -> SettingView:
        s = self._repo.get_setting(key)
        if s is None:
            raise NotFoundError(f"Setting '{key}' not found")
        return SettingView(setting=s, has_pending=s.pending is not None)

    def propose_setting(self, user: AppUser, key: str, value: Any) -> SettingView:
        require_role(user, CONFIG_ROLES, "change a setting")
        current = self.get_setting(key).setting
        self._check_type(current.value, value)
        if key == "low-relevance-threshold" and not 0 <= value <= 100:
            raise InvalidError("The threshold must be between 0 and 100.")
        if key == "lookback-days" and not 0 <= value <= 30:
            raise InvalidError("Lookback must be between 0 and 30 days.")
        updated = self._repo.propose_setting(key, value, user.name, user.name)
        return SettingView(setting=updated, has_pending=True)

    @staticmethod
    def _check_type(current: Any, new: Any) -> None:
        for kind, name in (
            (bool, "yes/no"),
            (int, "a whole number"),
            (str, "text"),
            (list, "a list"),
            (dict, "an object"),
        ):
            if isinstance(current, kind):
                if kind is int and isinstance(new, bool):
                    raise InvalidError(f"This setting must be {name}.")
                if not isinstance(new, kind):
                    raise InvalidError(f"This setting must be {name}.")
                return

    def _pending_setting(self, key: str) -> Any:
        setting = self.get_setting(key).setting
        if setting.pending is None:
            raise ConflictError("Nothing is waiting for approval for this setting.")
        return setting.pending

    def approve_setting(self, user: AppUser, key: str) -> SettingView:
        require_role(user, APPROVER_ROLES, "approve a setting change")
        pending = self._pending_setting(key)
        require_different_person(user, pending.requested_by, "approve this change")
        updated = self._repo.approve_setting(key, user.name, user.name)
        return SettingView(setting=updated, has_pending=False)

    def reject_setting(self, user: AppUser, key: str, reason: str) -> SettingView:
        require_role(user, APPROVER_ROLES, "reject a setting change")
        pending = self._pending_setting(key)
        require_different_person(user, pending.requested_by, "reject this change")
        if not reason.strip():
            raise InvalidError("A reason is required to reject a change.")
        updated = self._repo.reject_setting(key, reason.strip(), user.name)
        return SettingView(setting=updated, has_pending=False)
