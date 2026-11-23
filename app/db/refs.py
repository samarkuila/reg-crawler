"""Internal reference numbers: TVSC-REG-YYYY-NNNNNN, one counter per year.

`next_internal_ref` is one UPDATE ... RETURNING on the counter row, so two writers can never get
the same number: the database serialises them on that row (SQLite takes the write lock, PostgreSQL
a row lock). It runs inside the caller's transaction, so a rolled-back item gives its number back
and numbers stay gap-free.
"""

from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import InternalRefCounter

PREFIX = "TVSC-REG"


def format_ref(year: int, number: int) -> str:
    return f"{PREFIX}-{year:04d}-{number:06d}"


def next_internal_ref(session: Session, year: int) -> str:
    bump = (
        update(InternalRefCounter)
        .where(InternalRefCounter.year == year)
        .values(last_value=InternalRefCounter.last_value + 1)
        .returning(InternalRefCounter.last_value)
    )
    number = session.execute(bump).scalar()
    if number is None:  # first reference of the year: create the counter row
        try:
            with session.begin_nested():
                session.execute(insert(InternalRefCounter).values(year=year, last_value=0))
        except IntegrityError:
            pass  # another writer created it first; bump theirs
        number = session.execute(bump).scalar()
    assert number is not None  # noqa: S101 - the row exists now
    return format_ref(year, number)
