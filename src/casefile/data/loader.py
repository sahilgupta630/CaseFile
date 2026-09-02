"""Stage 0 — ingest and conform. §15 S0, §22.

*"Getting three departments to speak the same language. Finance keeps records by
invoice, Sales by deal, Support by ticket — and they disagree on customer names
and month boundaries."*

**In:** the three raw sources under `data/raw/`. **Out:** DuckDB tables sharing
`account_id`, `region`, `product_id` and a fiscal calendar, plus a watermark per
source. Nothing clever: an alias map and a calendar table, both small.

Two of those four keys involve an actual reconciliation, and both are the reason
this stage exists rather than being folded into a SELECT:

* **`account_id`** — billing writes `A0001`, CRM writes `ACC-0001`. Without the
  map the two sources simply do not join, and every cross-source claim in §14.2
  is unavailable.
* **`region`** — CRM is the system of record and overwrites an account's region
  when it is reassigned; billing keeps whatever it stamped on the line at the
  time. So the two genuinely disagree about the past. Billing's column is
  **replaced**, and the count of replacements lands in `meta.conformance` rather
  than being silently swallowed.

The watermark per source is §22's `max(_ingested_at | _synced_at)`. `product_ops`
has no ingest column in §22's schema; for a ≤15-minute stream the event time is
the arrival time, so its latest event is its watermark.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from casefile.data.scm import AS_OF, SPAN_END

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RAW = ROOT / "data" / "raw"
DEFAULT_DB = ROOT / "data" / "casefile.duckdb"
DEFAULT_ALIAS = ROOT / "data" / "account_alias.csv"
#: §14.1's first filter — "excludes intercompany and test accounts". Which
#: accounts those are is a business fact the CRM does not record, so it is
#: committed configuration in the same spirit as the alias map (§15 S0:
#: "entity-alias map + calendar table. Small, committed, not clever").
DEFAULT_TEST_ACCOUNTS = ROOT / "data" / "test_accounts.csv"

#: Which CSV becomes which table, and which column carries "when did this land".
#: `None` means the table has no ingest column of its own — §22 gives billing
#: `_ingested_at` and CRM `_synced_at`, and gives product_ops neither.
TABLES: dict[str, dict[str, str | None]] = {
    "billing": {
        "invoice": "_ingested_at",
        "invoice_line": "_ingested_at",
        "credit_note": "_ingested_at",
        "price_book": None,
    },
    "crm": {
        "account": "_synced_at",
        "opportunity": "_synced_at",
        "renewal": None,
        "opportunity_note": None,
    },
    "product_ops": {
        "ticket": "created_at",
        "ticket_message": "created_at",
        "deploy_event": "deployed_at",
        "incident": "started_at",
        "news_item": "published_at",
    },
}

#: §14.1's `calendar: fiscal_445`. The fiscal year opens on the Monday on or
#: before the contract's `history_start`, and each quarter runs 4 + 4 + 5 weeks.
FISCAL_ANCHOR = date(2023, 3, 27)  # the Monday on or before 2023-04-01
QUARTER_SHAPE = (4, 4, 5)


def build(
    raw_dir: Path | str = DEFAULT_RAW,
    db_path: Path | str = DEFAULT_DB,
    alias_path: Path | str = DEFAULT_ALIAS,
    test_accounts_path: Path | str = DEFAULT_TEST_ACCOUNTS,
    as_of: datetime | None = None,
) -> Path:
    """Load, conform, and record the watermarks. Rebuilds from scratch — the
    database is derived, `*.duckdb` is gitignored, and a loader that appends to
    whatever was there before is a loader nobody can reason about.

    `as_of` is the simulated "now" stamped into `meta.watermark.as_of` —
    defaults to `scm.AS_OF`, so every caller written before this parameter
    existed is unaffected. `data/ingest.py` passes its own later `as_of` after
    appending a new batch, which is what lets the simulated present actually
    advance rather than staying the one frozen constant forever.
    """
    raw_dir, db_path, alias_path = Path(raw_dir), Path(db_path), Path(alias_path)
    test_accounts_path = Path(test_accounts_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    con = duckdb.connect(str(db_path))
    try:
        for schema in (*TABLES, "meta"):
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

        _ingest(con, raw_dir, alias_path)
        _conform_accounts(con)
        _flag_test_accounts(con, test_accounts_path)
        _conform_regions(con)
        _fiscal_calendar(con)
        _watermarks(con, as_of or AS_OF)
    finally:
        con.close()
    return db_path


def connect(db_path: Path | str = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=True)


# ── Ingest ────────────────────────────────────────────────────────────────────


def _ingest(con: duckdb.DuckDBPyConnection, raw_dir: Path, alias_path: Path) -> None:
    for source, tables in TABLES.items():
        for table in tables:
            csv_path = raw_dir / source / f"{table}.csv"
            if not csv_path.exists():
                raise FileNotFoundError(f"{csv_path} is missing — run `make data` first")
            con.execute(
                f"CREATE TABLE {source}.{table} AS "
                "SELECT * FROM read_csv_auto(?, header = true, sample_size = -1)",
                [str(csv_path)],
            )
    con.execute(
        "CREATE TABLE meta.account_alias AS "
        "SELECT * FROM read_csv_auto(?, header = true)",
        [str(alias_path)],
    )


def _flag_test_accounts(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Add `crm.account.is_test`, so §14.1's first filter is executable.

    The contract's definition says net revenue *"excludes intercompany and test
    accounts"*. §22's CRM schema has no column for it — which is realistic, and
    is why this is a conformance step rather than generator output. Without the
    column the filter cannot compile, and a `filters` list the metric layer
    quietly ignores would make §14.1's "executable configuration, not
    documentation" untrue of the very first element it lists.
    """
    con.execute("CREATE TABLE meta.test_account AS SELECT * FROM read_csv_auto(?, header = true)",
                [str(path)])
    con.execute("ALTER TABLE crm.account ADD COLUMN is_test BOOLEAN")
    con.execute(
        "UPDATE crm.account SET is_test = "
        "EXISTS (SELECT 1 FROM meta.test_account t WHERE t.account_id = crm.account.account_id)"
    )
    flagged = con.execute("SELECT count(*) FROM crm.account WHERE is_test").fetchone()
    listed = con.execute("SELECT count(*) FROM meta.test_account").fetchone()
    assert flagged is not None and listed is not None
    if flagged[0] != listed[0]:
        raise ValueError(
            f"{path} names accounts that are not in crm.account — the exclusion list "
            "is stale, and a filter that silently excludes nothing is not a filter"
        )


# ── Conformance ───────────────────────────────────────────────────────────────


def _conform_accounts(con: duckdb.DuckDBPyConnection) -> None:
    """Billing's own key is kept as `source_account_id` rather than overwritten.

    An unmatched row would otherwise vanish into a NULL join key and be
    impossible to chase back to the file it came from — which is precisely the
    failure this stage is supposed to make impossible.
    """
    for table in ("invoice", "invoice_line", "credit_note"):
        con.execute(f"ALTER TABLE billing.{table} RENAME account_id TO source_account_id")
        con.execute(f"ALTER TABLE billing.{table} ADD COLUMN account_id VARCHAR")
        con.execute(
            f"""
            UPDATE billing.{table} AS t
               SET account_id = m.account_id
              FROM meta.account_alias AS m
             WHERE m.source = 'billing' AND m.raw_id = t.source_account_id
            """
        )

    unmatched = con.execute(
        "SELECT count(*) FROM billing.invoice_line WHERE account_id IS NULL"
    ).fetchone()
    assert unmatched is not None
    if unmatched[0]:
        raise ValueError(
            f"{unmatched[0]} billing lines have no alias entry — data/account_alias.csv "
            "is stale against data/raw/. Regenerate it rather than loosening the join."
        )


def _conform_regions(con: duckdb.DuckDBPyConnection) -> None:
    """CRM is the system of record for an account's region."""
    con.execute(
        """
        CREATE TABLE meta.conformance AS
        SELECT 'billing.invoice_line' AS relation,
               'region'               AS field,
               count(*)               AS overridden
          FROM billing.invoice_line AS l
          JOIN crm.account AS a USING (account_id)
         WHERE l.region IS DISTINCT FROM a.region
        """
    )
    con.execute("ALTER TABLE billing.invoice_line RENAME region TO source_region")
    con.execute("ALTER TABLE billing.invoice_line ADD COLUMN region VARCHAR")
    con.execute(
        """
        UPDATE billing.invoice_line AS l
           SET region = a.region
          FROM crm.account AS a
         WHERE a.account_id = l.account_id
        """
    )


# ── The calendar ──────────────────────────────────────────────────────────────


def _fiscal_calendar(con: duckdb.DuckDBPyConnection) -> None:
    """One row per date: fiscal year, quarter, period, and week.

    4-4-5 means twelve periods of 28 or 35 days, so a fiscal period is *not* a
    calendar month and the two drift apart across a year. Stage 2 aggregates on
    whichever the contract names, and `net_revenue` names this one.
    """
    con.execute(
        """
        CREATE TABLE meta.fiscal_calendar (
            day DATE, fiscal_year INTEGER, fiscal_quarter INTEGER,
            fiscal_period INTEGER, period_label VARCHAR, fiscal_week INTEGER
        )
        """
    )
    con.executemany(
        "INSERT INTO meta.fiscal_calendar VALUES (?, ?, ?, ?, ?, ?)", _calendar_rows()
    )


def _calendar_rows() -> list[tuple[str, int, int, int, str, int]]:
    rows: list[tuple[str, int, int, int, str, int]] = []
    week_start = FISCAL_ANCHOR
    fiscal_year = FISCAL_ANCHOR.year + 1

    while week_start <= SPAN_END + timedelta(days=365):
        week_of_year = 1
        for index, weeks_in_period in enumerate(QUARTER_SHAPE * 4):
            period = index + 1
            quarter = index // 3 + 1
            label = f"FY{fiscal_year}-P{period:02d}"
            for _ in range(weeks_in_period):
                for offset in range(7):
                    day = week_start + timedelta(days=offset)
                    rows.append(
                        (day.isoformat(), fiscal_year, quarter, period, label, week_of_year)
                    )
                week_start += timedelta(days=7)
                week_of_year += 1
        fiscal_year += 1
    return rows


# ── Watermarks ────────────────────────────────────────────────────────────────


def _watermarks(con: duckdb.DuckDBPyConnection, as_of: datetime) -> None:
    """§22: one per source, `max(_ingested_at | _synced_at)`.

    Per *source*, not per table — a source is what has a refresh cadence, and
    §15 S1 checks freshness against the cadence the contract declares.
    """
    con.execute(
        """
        CREATE TABLE meta.watermark (
            source VARCHAR, watermark TIMESTAMP, row_count BIGINT, as_of TIMESTAMP
        )
        """
    )
    for source, tables in TABLES.items():
        parts = [
            f"SELECT max({column}::TIMESTAMP) AS w, count(*) AS n FROM {source}.{table}"
            for table, column in tables.items()
            if column is not None
        ]
        con.execute(
            f"""
            INSERT INTO meta.watermark
            SELECT '{source}', max(w), sum(n), ?::TIMESTAMP
              FROM ({" UNION ALL ".join(parts)})
            """,
            [as_of.isoformat()],
        )


def main() -> None:
    path = build()
    con = connect(path)
    try:
        for source, mark, count, _ in con.execute(
            "SELECT * FROM meta.watermark ORDER BY source"
        ).fetchall():
            print(f"{source:14} watermark {mark}  {count:,} rows")
    finally:
        con.close()


if __name__ == "__main__":
    main()
