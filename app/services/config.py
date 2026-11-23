"""Config service: sources and settings as versioned data with maker–checker approval.

A change never applies directly: it becomes a PENDING version that a *different* approver must
approve (CLAUDE.md rule 9). Unapproved versions are never used by crawls or by the email.
"""

import re
from datetime import datetime
from typing import Any

from app.repositories.base import Repository
from app.schemas.api import (
    FieldChange,
    SettingView,
    SmeFunctionOption,
    SourceChanges,
    SourceCreate,
    SourceTestResult,
    SourceView,
)
from app.schemas.entities import AppUser, Setting, Source, SourceVersion, now_ist
from app.schemas.enums import SourceType, VersionStatus
from app.services import readable
from app.services.errors import ConflictError, InvalidError, NotFoundError
from app.services.permissions import (
    APPROVER_ROLES,
    CONFIG_ROLES,
    require_different_person,
    require_role,
)
from app.services.source_test import MockSourceTester, SourceTester

_HOW = {
    SourceType.RSS: "RSS feed + HTML listing",
    SourceType.HTML_LIST: "HTML listing",
    SourceType.STRUCTURED_DATA: "Official data file",
    SourceType.BROWSER: "Browser-rendered page",
}
SETTING_TITLES = {
    "run-times": "Run times",
    "working-days": "Working days",
    "holidays": "Holidays",
    "lookback-days": "Lookback days",
    "sme-routing": "SME routing",
    "recipients": "Recipients",
    "email-template": "Email template",
    "dismiss-reasons": "Dismiss reasons",
    "relevance-keywords": "Relevance keywords",
    "exclusion-keywords": "Exclusion keywords",
    "low-relevance-threshold": "Low-relevance threshold",
    "screening-guidance": "Screening guidance",
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _clean_list(value: Any, what: str, *, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidError(f"{what} must be a list of text values.")
    out = list(dict.fromkeys(v.strip() for v in value if v.strip()))
    if len(out) < minimum:
        raise InvalidError(f"{what}: add at least {minimum}.")
    return out


class ConfigService:
    def __init__(self, repo: Repository, tester: SourceTester | None = None) -> None:
        self._repo = repo
        self._tester: SourceTester = tester or MockSourceTester()

    def list_sme_functions(self) -> list[SmeFunctionOption]:
        """Active SME functions for the review form's dropdown (id + name only)."""
        return [
            SmeFunctionOption(id=f.id, name=f.name)
            for f in self._repo.list_sme_functions()
            if f.active
        ]

    # --- who may decide ----------------------------------------------------------------------

    @staticmethod
    def _decision_rights(user: AppUser, requested_by: str | None) -> tuple[bool, str | None]:
        if requested_by is None:
            return False, None
        if user.role not in APPROVER_ROLES:
            return False, "Only an approver can approve or reject this change."
        if user.name == requested_by:
            return False, f"{requested_by} made this change, so a different approver must decide."
        return True, None

    # --- sources -----------------------------------------------------------------------------

    def _source(self, source_id: str) -> Source:
        source = self._repo.get_source(source_id)
        if source is None:
            raise NotFoundError(f"Source {source_id} not found")
        return source

    def _pending_version(self, source_id: str) -> SourceVersion | None:
        pending = [
            v
            for v in self._repo.list_source_versions(source_id)
            if v.status == VersionStatus.PENDING
        ]
        return pending[-1] if pending else None

    def _view(self, source: Source, user: AppUser) -> SourceView:
        pending = self._pending_version(source.id)
        if source.active:
            label = "Active"
        elif source.planned:
            label = "Planned (V2)"
        elif source.current_version == 0:
            label = "Waiting for approval"
        else:
            label = "Inactive"
        can_decide, note = self._decision_rights(user, pending.requested_by if pending else None)
        return SourceView(
            source=source,
            how_we_read_it=(
                _HOW[SourceType.RSS]
                if source.rss_url and source.type == SourceType.HTML_LIST
                else _HOW[source.type]
            ),
            status_label=label,
            pending_version=pending,
            pending_changes=readable.diff_fields(pending.before, pending.after) if pending else [],
            can_edit=user.role in CONFIG_ROLES,
            can_decide=can_decide,
            decide_note=note,
        )

    def list_sources(self, user: AppUser) -> list[SourceView]:
        sources = sorted(
            self._repo.list_sources(), key=lambda s: (not s.active, s.planned, s.authority.lower())
        )
        return [self._view(s, user) for s in sources]

    @staticmethod
    def _check_source_fields(fields: dict[str, Any]) -> dict[str, Any]:
        """Validate and tidy the fields a person typed. Returns the cleaned fields."""
        out = dict(fields)
        for key in ("authority", "page_name"):
            if key in out:
                out[key] = (out[key] or "").strip()
                if not out[key]:
                    raise InvalidError("Authority and page name cannot be empty.")
        for key in ("url", "rss_url"):
            if key in out:
                out[key] = (out[key] or "").strip() or None
                if out[key] and not re.match(r"^https?://\S+$", out[key]):
                    raise InvalidError("Web addresses must start with http:// or https://.")
                if key == "url" and not out[key]:
                    raise InvalidError("The page URL is required.")
        for key in ("include_keywords", "exclude_keywords"):
            if key in out:
                out[key] = _clean_list(out[key], "Keywords")
        cfg = out.get("adapter_config")
        if cfg is not None:
            if not isinstance(cfg, dict):
                raise InvalidError("Reading rules must be an object.")
            selectors = cfg.get("selectors")
            if selectors is not None:
                if not isinstance(selectors, dict):
                    raise InvalidError("Selectors must be an object.")
                for name, val in selectors.items():
                    if not isinstance(val, str):
                        raise InvalidError(f"The {name} selector must be text.")
                pattern = selectors.get("ref_no_regex")
                if pattern:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        raise InvalidError(
                            f"The reference no. pattern is not valid: {exc}"
                        ) from exc
            fmt = cfg.get("date_format")
            if fmt:
                if "%" not in fmt:
                    raise InvalidError("The date format needs codes such as %d.%m.%Y.")
                try:
                    datetime(2026, 11, 20).strftime(fmt)
                except ValueError as exc:
                    raise InvalidError("The date format is not valid.") from exc
        return out

    def create_source(self, user: AppUser, body: SourceCreate) -> SourceView:
        require_role(user, CONFIG_ROLES, "add a source")
        fields = self._check_source_fields(body.model_dump(mode="json", exclude={"id"}))
        source_id = body.id or _slug(f"{fields['authority']}-{fields['page_name']}")
        if not re.match(r"^[a-z0-9][a-z0-9-]{1,60}$", source_id):
            raise InvalidError("Could not make an id from the authority and page name.")
        if self._repo.get_source(source_id) is not None:
            raise ConflictError(f"A source with id '{source_id}' already exists.")
        source = Source(
            id=source_id,
            active=False,
            current_version=0,
            **{**fields, "type": SourceType(fields["type"]), "priority": fields["priority"]},
        )
        version = SourceVersion(
            id=f"srcv-{source_id}-1",
            source_id=source_id,
            version=1,
            before={},
            after={**fields, "active": True},
            status=VersionStatus.PENDING,
            requested_by=user.name,
            ts=now_ist(),
        )
        return self._view(self._repo.create_source(source, version, user.name), user)

    def list_versions(self, source_id: str) -> list[SourceVersion]:
        self._source(source_id)
        return self._repo.list_source_versions(source_id)

    def propose_version(
        self, user: AppUser, source_id: str, changes: SourceChanges
    ) -> SourceVersion:
        require_role(user, CONFIG_ROLES, "change a source")
        source = self._source(source_id)
        if self._pending_version(source_id) is not None:
            raise ConflictError(
                "A change to this source is already waiting for approval. "
                "Approve or reject it before proposing another."
            )
        after = self._check_source_fields(changes.model_dump(mode="json", exclude_unset=True))
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
        """Dry run: read one page with the source's reading rules and return the parsed rows.

        If a change is waiting for approval, the test uses the proposed rules, so they can be
        checked before approving. Nothing is saved and no audit event is written.
        """
        require_role(user, CONFIG_ROLES, "test a source")
        source = self._source(source_id)
        pending = self._pending_version(source_id)
        if pending is not None:
            changes = {k: v for k, v in pending.after.items() if k in Source.model_fields}
            source = Source.model_validate({**source.model_dump(), **changes})
        return self._tester.run(source)

    # --- settings ----------------------------------------------------------------------------

    def _setting_view(self, s: Setting, user: AppUser) -> SettingView:
        title = SETTING_TITLES.get(s.key, s.key.replace("-", " ").capitalize())
        changes: list[FieldChange] = []
        if s.pending is not None:
            changes = self._setting_changes(title, s.value, s.pending.value)
        can_decide, note = self._decision_rights(
            user, s.pending.requested_by if s.pending else None
        )
        return SettingView(
            setting=s,
            has_pending=s.pending is not None,
            title=title,
            changes=changes,
            can_edit=user.role in CONFIG_ROLES,
            can_decide=can_decide,
            decide_note=note,
        )

    @staticmethod
    def _setting_changes(title: str, before: Any, after: Any) -> list[FieldChange]:
        if isinstance(before, dict) and isinstance(after, dict):
            return readable.diff_fields(before, after)
        if isinstance(before, list) and isinstance(after, list):
            return readable.diff_lists(before, after)
        return [FieldChange(label=title, before=readable.text(before), after=readable.text(after))]

    def list_settings(self, user: AppUser) -> list[SettingView]:
        return [self._setting_view(s, user) for s in self._repo.list_settings()]

    def get_setting(self, user: AppUser, key: str) -> SettingView:
        s = self._repo.get_setting(key)
        if s is None:
            raise NotFoundError(f"Setting '{key}' not found")
        return self._setting_view(s, user)

    def propose_setting(self, user: AppUser, key: str, value: Any) -> SettingView:
        require_role(user, CONFIG_ROLES, "change a setting")
        current = self.get_setting(user, key).setting
        if current.pending is not None:
            raise ConflictError(
                "A change to this setting is already waiting for approval. "
                "Approve or reject it before proposing another."
            )
        self._check_type(current.value, value)
        value = self._normalise(key, current.value, value)
        if value == current.value:
            raise InvalidError("Nothing would change. Edit at least one field.")
        updated = self._repo.propose_setting(key, value, user.name, user.name)
        return self._setting_view(updated, user)

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

    # --- per-setting checks (what a valid value looks like) ------------------------------------

    def _normalise(self, key: str, current: Any, value: Any) -> Any:  # noqa: PLR0911, PLR0912
        if key == "low-relevance-threshold" and not 0 <= value <= 100:
            raise InvalidError("The threshold must be between 0 and 100.")
        if key == "lookback-days" and not 0 <= value <= 30:
            raise InvalidError("Lookback must be between 0 and 30 days.")
        if key == "run-times":
            return self._run_times(value)
        if key == "working-days":
            days = [str(d).upper() for d in _clean_list(value, "Working days", minimum=1)]
            if any(d not in _DAYS for d in days):
                raise InvalidError("Working days must be MON, TUE, WED, THU, FRI, SAT or SUN.")
            return [d for d in _DAYS if d in days]
        if key == "holidays":
            return self._holidays(value)
        if key == "dismiss-reasons":
            return _clean_list(value, "Dismiss reasons", minimum=1)
        if key in ("relevance-keywords", "exclusion-keywords"):
            return _clean_list(value, "Keywords")
        if key == "sme-routing":
            return self._sme(current, value)
        if key == "recipients":
            return self._recipients(current, value)
        if key == "email-template":
            return self._template(current, value)
        if key == "screening-guidance":
            text = value.get("text")
            if not isinstance(text, str) or len(text) > 5000:
                raise InvalidError("Guidance must be text of up to 5000 characters.")
            return {"text": text.strip()}
        return value

    @staticmethod
    def _run_times(value: dict[str, Any]) -> dict[str, str]:
        names = ["morning", "eod", "urgent_hourly_from", "urgent_hourly_to"]
        if set(value) != set(names):
            raise InvalidError("Give all four run times.")
        for n in names:
            if not isinstance(value[n], str) or not _TIME.match(value[n]):
                raise InvalidError(f"{readable.label(n)} must be a time like 19:30.")
        if value["urgent_hourly_from"] >= value["urgent_hourly_to"]:
            raise InvalidError("Hourly checks must start before they end.")
        return {n: value[n] for n in names}

    @staticmethod
    def _holidays(value: Any) -> list[str]:
        out = []
        for raw in _clean_list(value, "Holidays"):
            try:
                day = datetime.strptime(raw, "%d.%m.%Y")
            except ValueError as exc:
                raise InvalidError(f"'{raw}' is not a date. Use DD.MM.YYYY.") from exc
            out.append(day.strftime("%d.%m.%Y"))
        return list(dict.fromkeys(out))

    @staticmethod
    def _sme(current: list[dict[str, Any]], value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise InvalidError("SME routing must be a list of functions.")
        out, seen = [], set()
        for item in value:
            name = str(item.get("name") or "").strip()
            if not name:
                raise InvalidError("Every function needs a name.")
            fid = str(item.get("id") or "").strip() or _slug(name)
            if not fid or fid in seen:
                raise InvalidError(f"'{name}' is listed twice.")
            seen.add(fid)
            email = str(item.get("head_email") or "").strip() or None
            if email and not _EMAIL.match(email):
                raise InvalidError(f"'{email}' is not a valid email address.")
            out.append(
                {
                    "id": fid,
                    "name": name,
                    "head_name": str(item.get("head_name") or "").strip() or None,
                    "head_email": email,
                    "active": bool(item.get("active", True)),
                }
            )
        missing = {c["id"] for c in current} - seen
        if missing:
            raise InvalidError(
                "A function cannot be removed because past decisions refer to it. "
                "Mark it inactive instead."
            )
        return out

    @staticmethod
    def _recipients(current: list[dict[str, Any]], value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise InvalidError("Recipients must be a list.")
        out, seen = [], set()
        for item in value:
            name = str(item.get("name") or "").strip()
            email = str(item.get("email") or "").strip()
            kind = str(item.get("kind") or "TO").upper()
            if not name or not _EMAIL.match(email):
                raise InvalidError("Every recipient needs a name and a valid email address.")
            if kind not in ("TO", "CC"):
                raise InvalidError("Choose To or CC for every recipient.")
            rid = str(item.get("id") or "").strip() or f"rcp-{_slug(name)}"
            if rid in seen:
                raise InvalidError(f"'{name}' is listed twice.")
            seen.add(rid)
            out.append(
                {
                    "id": rid,
                    "name": name,
                    "email": email,
                    "kind": kind,
                    "active": bool(item.get("active", True)),
                }
            )
        if not any(r["active"] and r["kind"] == "TO" for r in out):
            raise InvalidError("At least one active recipient is needed in To.")
        return out

    @staticmethod
    def _template(current: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
        fields = [
            "subject_pattern",
            "greeting",
            "intro",
            "disclaimer",
            "no_update_text",
            "sign_off",
            "sender_name",
        ]
        out = {"name": current.get("name", "Daily regulatory update")}
        for f in fields:
            text = value.get(f)
            if not isinstance(text, str) or not text.strip():
                raise InvalidError(f"{readable.label(f)} cannot be empty.")
            out[f] = text.strip()
        try:
            out["subject_pattern"].format(date="20.11.2026", cycle="EOD")
            out["no_update_text"].format(date="20.11.2026")
        except (KeyError, IndexError, ValueError) as exc:
            raise InvalidError(
                "Only {date} and {cycle} can be used in the subject, and only {date} in the "
                "no-update sentence."
            ) from exc
        return out

    # --- approval ----------------------------------------------------------------------------

    def _pending_setting(self, user: AppUser, key: str) -> Any:
        setting = self.get_setting(user, key).setting
        if setting.pending is None:
            raise ConflictError("Nothing is waiting for approval for this setting.")
        return setting.pending

    def approve_setting(self, user: AppUser, key: str) -> SettingView:
        require_role(user, APPROVER_ROLES, "approve a setting change")
        pending = self._pending_setting(user, key)
        require_different_person(user, pending.requested_by, "approve this change")
        updated = self._repo.approve_setting(key, user.name, user.name)
        return self._setting_view(updated, user)

    def reject_setting(self, user: AppUser, key: str, reason: str) -> SettingView:
        require_role(user, APPROVER_ROLES, "reject a setting change")
        pending = self._pending_setting(user, key)
        require_different_person(user, pending.requested_by, "reject this change")
        if not reason.strip():
            raise InvalidError("A reason is required to reject a change.")
        updated = self._repo.reject_setting(key, reason.strip(), user.name)
        return self._setting_view(updated, user)
