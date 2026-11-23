"""POC users. V2 replaces the switcher with Entra ID; roles and checks stay in the services."""

from app.repositories.base import Repository
from app.schemas.entities import AppUser

DEFAULT_USER_ID = "u-divya"


class UserService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def list_users(self) -> list[AppUser]:
        return self._repo.list_users()

    def get(self, user_id: str | None) -> AppUser | None:
        return self._repo.get_user(user_id) if user_id else None

    def resolve(self, user_id: str | None) -> AppUser:
        """The selected user, or the default POC reviewer if the cookie is missing or unknown."""
        user = self.get(user_id) or self.get(DEFAULT_USER_ID)
        return user or self._repo.list_users()[0]
