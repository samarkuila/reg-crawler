"""Role checks. Enforced in services, never only in the UI (CLAUDE.md, POC user switcher)."""

from app.schemas.entities import AppUser
from app.schemas.enums import UserRole
from app.services.errors import ForbiddenError

REVIEW_ROLES = frozenset({UserRole.REVIEWER, UserRole.APPROVER})
CONFIG_ROLES = frozenset({UserRole.ADMIN, UserRole.APPROVER})
APPROVER_ROLES = frozenset({UserRole.APPROVER})
RUN_ROLES = frozenset({UserRole.ADMIN, UserRole.REVIEWER, UserRole.APPROVER})

_ROLE_NAMES = {
    UserRole.VIEWER: "viewer",
    UserRole.REVIEWER: "reviewer",
    UserRole.APPROVER: "approver",
    UserRole.ADMIN: "admin",
}


def require_role(user: AppUser, allowed: frozenset[UserRole], what: str) -> None:
    if user.role not in allowed:
        needed = " or ".join(sorted(_ROLE_NAMES[r] for r in allowed))
        raise ForbiddenError(
            f"{user.name} is a {_ROLE_NAMES[user.role]}. Only a {needed} can {what}."
        )


def require_different_person(user: AppUser, requester: str, what: str) -> None:
    """Maker–checker: the approver must not be the person who made the change."""
    if user.name == requester:
        raise ForbiddenError(f"A different person must {what}. {requester} made this change.")
