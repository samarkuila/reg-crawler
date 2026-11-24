"""Crawl errors are shown to reviewers in plain words; the raw text stays in the log."""

import pytest

from app.services.readable import plain_error

NOTE = (
    "Not baselined: a channel was blocked, failed, empty or could not be read; "
    "the next crawl applies the window again"
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "DownloadTimeoutError: Getting https://x.test/a took longer than 30.0 seconds.; "
            + NOTE,
            "The website did not answer within 30.0 seconds.",
        ),
        (
            "Forbidden by robots.txt; " + NOTE,
            "The website's robots.txt does not allow automated reading. Nothing was bypassed; "
            "check the page by hand.",
        ),
        (
            "DownloadFailedError: [<twisted.python.failure.Failure "
            "twisted.internet.error.ConnectionLost: Connection lost.>]; " + NOTE,
            "The connection to the website was lost.",
        ),
        ("HTTP 404; " + NOTE, "Page not found (HTTP 404); the address may have changed."),
        (
            "javascript:__doPostBack('lnk_Extra_All',''): download failed (Not downloaded)",
            "A menu link that is not a document was skipped.",
        ),
    ],
)
def test_known_errors_are_plain(raw, expected) -> None:
    assert plain_error(raw) == expected


def test_unknown_text_is_kept_and_empty_stays_empty() -> None:
    assert plain_error("Something odd happened") == "Something odd happened."
    assert plain_error(None) is None and plain_error("") == ""
