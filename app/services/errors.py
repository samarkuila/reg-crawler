"""Domain errors raised by services. The API layer maps them to HTTP responses."""


class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidError(DomainError):
    status_code = 422


class ForbiddenError(DomainError):
    status_code = 403


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class TooLargeError(DomainError):
    status_code = 413


class NotImplementedYet(DomainError):
    status_code = 501
