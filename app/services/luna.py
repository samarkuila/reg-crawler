"""Luna on the screen: status, "Ask Luna", and the flag list. All advisory.

Nothing here applies a suggestion. "Use suggestion" in the UI only copies a value into the review
form; the reviewer still has to Accept, Save or Dismiss (policy A1, A9).
"""

import os
from typing import TYPE_CHECKING

from app.agents import settings as agent_settings
from app.repositories.base import Repository
from app.schemas.api import AgentFlagView, AgentStatusOut, ItemDetail
from app.schemas.entities import AppUser
from app.schemas.enums import FlagStatus
from app.services.errors import ConflictError, NotFoundError
from app.services.llm import LlmConfig, todays_spend
from app.services.permissions import REVIEW_ROLES, require_role
from app.services.review import ReviewService

if TYPE_CHECKING:
    from app.agents.orchestrator import AgentOrchestrator


class LunaService:
    def __init__(
        self, repo: Repository, orchestrator: "AgentOrchestrator", review: ReviewService
    ) -> None:
        self._repo = repo
        self._orchestrator = orchestrator
        self._review = review

    def status(self) -> AgentStatusOut:
        setting = self._repo.get_setting("agent-enabled")
        problem = LlmConfig.from_env(dict(os.environ)).problem()
        return AgentStatusOut(
            enabled=agent_settings.agent_enabled(self._repo),
            env_enabled=agent_settings.env_enabled(),
            setting_enabled=True if setting is None else bool(setting.value),
            configured=problem is None,
            problem=problem,
            daily_budget_inr=agent_settings.daily_budget(self._repo),
            spent_today_inr=round(todays_spend(self._repo), 2),
            open_flags=len(self._repo.list_agent_flags(status=FlagStatus.OPEN)),
        )

    def ask(self, user: AppUser, item_id: str) -> ItemDetail:
        """ "Ask Luna": screen and extract this one item now."""
        require_role(user, REVIEW_ROLES, "ask Luna")
        if self._repo.get_item(item_id) is None:
            raise NotFoundError(f"Item {item_id} not found")
        if not self._orchestrator.is_enabled():
            raise ConflictError(
                "Luna is switched off. Ask the admin to turn it on in Settings "
                "(and AGENT_LUNA_ENABLED in the environment)."
            )
        summary = self._orchestrator.ask(item_id)
        if summary.stopped_because == "budget":
            raise ConflictError("Luna is unavailable: today's budget is used up.")
        if summary.screened == 0 and summary.extracted == 0:
            raise ConflictError(
                "Luna could not give a usable answer for this item. Please try again."
            )
        return self._review.item_detail(item_id)

    def flags(
        self, source_id: str | None = None, status: FlagStatus | None = FlagStatus.OPEN
    ) -> list[AgentFlagView]:
        sources = {s.id: s for s in self._repo.list_sources()}
        out = []
        for f in self._repo.list_agent_flags(source_id=source_id, status=status):
            source = sources.get(f.source_id or "")
            item = self._repo.get_item(f.item_id) if f.item_id else None
            out.append(
                AgentFlagView(
                    id=f.id, type=f.type.value, status=f.status.value, source_id=f.source_id,
                    authority=source.authority if source else None,
                    page_name=source.page_name if source else None,
                    item_id=f.item_id, item_title=item.title if item else None,
                    snapshot_id=f.snapshot_id, payload=f.payload, created_at=f.created_at,
                    resolved_by=f.resolved_by, resolved_at=f.resolved_at,
                )
            )  # fmt: skip
        return out

    def resolve(self, user: AppUser, flag_id: str, status: FlagStatus) -> AgentFlagView:
        require_role(user, REVIEW_ROLES, "close a Luna flag")
        flag = next((f for f in self._repo.list_agent_flags() if f.id == flag_id), None)
        if flag is None:
            raise NotFoundError(f"Flag {flag_id} not found")
        self._repo.resolve_agent_flag(flag_id, status, user.name, user.name)
        return next(v for v in self.flags(status=None) if v.id == flag_id)
