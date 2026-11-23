# Moving from SQLite to PostgreSQL

The models and migrations are written to work on both. Moving is a configuration change plus one
data copy. This page lists what is different and gives the trigger SQL.

> **Status:** the PostgreSQL SQL below is included in migration `0001` and has **not been run
> against a PostgreSQL server yet** (the POC has only SQLite). Run the checks at the end on a
> test server before relying on it.

## Steps

1. Create an empty database and a user that owns it.
2. Set `DATABASE_URL=postgresql+psycopg://user:password@host:5432/regmonitor` (install the
   `psycopg` driver first). Never commit this value.
3. Run `.\tasks.ps1 migrate`. Migration `0001` creates the tables, indexes and the append-only
   triggers. On a dialect it has no trigger SQL for, it stops with an error instead of leaving the
   evidence tables unprotected.
4. Copy data from the SQLite file in this order (parents before children): `app_user`, `setting`,
   `sme_function`, `recipient`, `email_template`, `source`, `source_version`, `crawl_run`,
   `snapshot`, `source_check`, `manual_check`, `regulatory_item`, `internal_ref_counter`,
   `item_observation`, `document`, `review_decision`, `daily_update`, `daily_update_item`,
   `audit_event`.
   - Plain `INSERT`s into `snapshot`, `document` and `audit_event` are allowed by the triggers, so
     loading them needs no special handling. Only UPDATE, DELETE and TRUNCATE are blocked.
   - Set the counter so new references continue the sequence:
     `internal_ref_counter.last_value` must be at least the highest `NNNNNN` used in that year.
5. Copy the `data/snapshots` and `data/documents` folders to the new storage; the paths and
   SHA-256 values in the tables stay valid.

## Append-only triggers (PostgreSQL)

These are exactly what migration `0001` runs on PostgreSQL.

```sql
CREATE FUNCTION reject_append_only_change() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_snapshot_no_update    BEFORE UPDATE   ON snapshot    FOR EACH ROW       EXECUTE FUNCTION reject_append_only_change();
CREATE TRIGGER trg_snapshot_no_delete    BEFORE DELETE   ON snapshot    FOR EACH ROW       EXECUTE FUNCTION reject_append_only_change();
CREATE TRIGGER trg_snapshot_no_truncate  BEFORE TRUNCATE ON snapshot    FOR EACH STATEMENT EXECUTE FUNCTION reject_append_only_change();

CREATE TRIGGER trg_document_no_update    BEFORE UPDATE   ON document    FOR EACH ROW       EXECUTE FUNCTION reject_append_only_change();
CREATE TRIGGER trg_document_no_delete    BEFORE DELETE   ON document    FOR EACH ROW       EXECUTE FUNCTION reject_append_only_change();
CREATE TRIGGER trg_document_no_truncate  BEFORE TRUNCATE ON document    FOR EACH STATEMENT EXECUTE FUNCTION reject_append_only_change();

CREATE TRIGGER trg_audit_event_no_update   BEFORE UPDATE   ON audit_event FOR EACH ROW       EXECUTE FUNCTION reject_append_only_change();
CREATE TRIGGER trg_audit_event_no_delete   BEFORE DELETE   ON audit_event FOR EACH ROW       EXECUTE FUNCTION reject_append_only_change();
CREATE TRIGGER trg_audit_event_no_truncate BEFORE TRUNCATE ON audit_event FOR EACH STATEMENT EXECUTE FUNCTION reject_append_only_change();
```

The SQLite version is one trigger per statement, for example:

```sql
CREATE TRIGGER trg_snapshot_no_update BEFORE UPDATE ON snapshot
BEGIN SELECT RAISE(ABORT, 'snapshot is append-only: UPDATE is not allowed'); END;
```

A database administrator with enough rights can still drop a trigger. For production, give the
application user only `SELECT` and `INSERT` on these three tables as well, and keep the owner role
away from the application.

## Differences to know about

| Topic | SQLite (POC) | PostgreSQL |
|---|---|---|
| Foreign keys | enforced because the app sets `PRAGMA foreign_keys=ON` on each connection | always enforced |
| Enums | text + `CHECK` | the same (no native `ENUM`) |
| JSON | stored as text | `json` |
| Datetimes | stored as UTC text; `TZDateTime` restores the zone | `timestamptz` |
| Reference counter | `UPDATE ... RETURNING` takes the database write lock | takes a row lock on the year's counter row |
| Concurrent writers | one writer at a time; `busy_timeout` makes others wait | many writers |
| Changing a column later | Alembic "batch mode" rebuilds the table | normal `ALTER TABLE` |

## Checks to run on a test PostgreSQL server

1. `.\tasks.ps1 migrate` finishes and `alembic downgrade base` then `upgrade head` works.
2. `UPDATE snapshot SET url = url;`, `DELETE FROM audit_event;` and `TRUNCATE document;` each fail
   with "is append-only".
3. Run the reference-number test with many parallel workers: numbers are unique and gap-free.
4. Run the contract tests (Prompt 8) against the PostgreSQL URL.
