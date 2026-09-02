"""Ladder step 0.8 — Stage 0, conformance and watermarks.

The step's verify command from §44:

    "Three sources join on account_id; one watermark per source"

Both halves are here. The rest defend the two conformance keys that involve an
actual reconciliation — a conform step that never changes a value is a SELECT
wearing a stage's name — and the fiscal calendar §14.1 declares.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

from casefile.data.loader import FISCAL_ANCHOR, QUARTER_SHAPE, TABLES, build, connect
from casefile.data.scm import AS_OF, OPS_START, SPAN_END, SPAN_START

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.gate0


@pytest.fixture(scope="module")
def con(warehouse: Path) -> duckdb.DuckDBPyConnection:
    connection = connect(warehouse)
    yield connection
    connection.close()


def one(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


# ── Three sources join on account_id — the ladder's first verify ──────────────


def test_all_three_sources_join_on_account_id(con: duckdb.DuckDBPyConnection) -> None:
    """Billing writes `A0001`, CRM writes `ACC-0001`. Without the alias map the
    two do not join at all, and every cross-source claim in §14.2 — a ticket
    spike explaining a revenue drop — is simply unavailable."""
    joined = one(
        con,
        """
        SELECT count(*) FROM crm.account a
         WHERE EXISTS (SELECT 1 FROM billing.invoice_line l WHERE l.account_id = a.account_id)
           AND EXISTS (SELECT 1 FROM product_ops.ticket t WHERE t.account_id = a.account_id)
        """,
    )
    assert joined == one(con, "SELECT count(*) FROM crm.account") == 120


@pytest.mark.parametrize("table", ["invoice", "invoice_line", "credit_note"])
def test_no_billing_row_is_left_unconformed(
    con: duckdb.DuckDBPyConnection, table: str
) -> None:
    assert one(con, f"SELECT count(*) FROM billing.{table} WHERE account_id IS NULL") == 0


def test_the_original_key_survives_for_traceability(con: duckdb.DuckDBPyConnection) -> None:
    """Billing's own id is kept rather than overwritten. A row that failed to
    conform has to remain traceable to the file it came from, or the conformance
    error becomes a NULL nobody can chase."""
    assert one(
        con,
        "SELECT count(*) FROM billing.invoice_line "
        "WHERE source_account_id IS NULL OR source_account_id = account_id",
    ) == 0


def test_the_alias_map_is_complete_and_one_to_one(con: duckdb.DuckDBPyConnection) -> None:
    """A stale map is how this rots silently: new accounts appear, the join
    quietly drops them, and the totals are wrong rather than missing."""
    assert one(con, "SELECT count(*) FROM meta.account_alias") == 120
    assert one(con, "SELECT count(DISTINCT raw_id) FROM meta.account_alias") == 120
    assert one(con, "SELECT count(DISTINCT account_id) FROM meta.account_alias") == 120
    assert (
        one(
            con,
            "SELECT count(*) FROM (SELECT DISTINCT source_account_id FROM billing.invoice_line) s "
            "LEFT JOIN meta.account_alias m ON m.raw_id = s.source_account_id "
            "WHERE m.account_id IS NULL",
        )
        == 0
    )


# ── One watermark per source — the ladder's second verify ─────────────────────


def test_there_is_exactly_one_watermark_per_source(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute("SELECT source, watermark, row_count FROM meta.watermark").fetchall()
    assert {r[0] for r in rows} == {"billing", "crm", "product_ops"}
    assert len(rows) == 3
    for source, mark, count in rows:
        assert mark is not None, f"{source} has no watermark"
        assert count > 0, f"{source} loaded no rows"


def test_every_watermark_is_fresh_at_as_of(con: duckdb.DuckDBPyConnection) -> None:
    """§15 S1 reads these. The P0 audit found all three stale — billing 52.8h
    against a 26h SLA, CRM accounts 31,135h — which would have made scenario A
    provisional at 1.3, contradicting §25."""
    for source, mark in con.execute("SELECT source, watermark FROM meta.watermark").fetchall():
        age = (AS_OF - mark).total_seconds() / 3600
        assert 0 < age <= 26, f"{source} watermark is {age:.1f}h old at AS_OF"


def test_product_ops_is_the_short_history(con: duckdb.DuckDBPyConnection) -> None:
    """§22 gives it eight months against billing's thirty-six. Scenario C's
    peer-borrowed baseline at 1.3 has nothing to fire on otherwise."""
    first_ticket = one(con, "SELECT min(created_at)::DATE FROM product_ops.ticket")
    first_invoice = one(con, "SELECT min(invoice_date)::DATE FROM billing.invoice_line")

    assert first_ticket >= OPS_START
    assert first_invoice <= SPAN_START + (date(2023, 6, 1) - date(2023, 5, 1))
    assert (SPAN_END - first_invoice).days > 1000


# ── Region: conformance that actually changes something ───────────────────────


def test_billing_region_disagreements_were_found_and_overridden(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Six accounts were reassigned between regions mid-history. CRM overwrites
    the account's region; billing keeps what it stamped on the line at the time.
    A conform step that never overrides anything is not conformance — so this
    asserts both that the disagreement existed and that it is now gone.
    """
    overridden = one(
        con,
        "SELECT overridden FROM meta.conformance "
        "WHERE relation = 'billing.invoice_line' AND field = 'region'",
    )
    assert overridden > 0, "nothing to conform means the test proves nothing"

    remaining = one(
        con,
        "SELECT count(*) FROM billing.invoice_line l JOIN crm.account a USING (account_id) "
        "WHERE l.region IS DISTINCT FROM a.region",
    )
    assert remaining == 0
    assert one(con, "SELECT count(*) FROM billing.invoice_line WHERE source_region IS NULL") == 0


def test_crm_is_the_system_of_record_for_region(con: duckdb.DuckDBPyConnection) -> None:
    assert (
        one(
            con,
            "SELECT count(DISTINCT l.account_id) FROM billing.invoice_line l "
            "WHERE l.source_region IS DISTINCT FROM l.region",
        )
        > 0
    )


# ── The fiscal calendar §14.1 declares ────────────────────────────────────────


def test_the_calendar_covers_every_day_exactly_once(con: duckdb.DuckDBPyConnection) -> None:
    total = one(con, "SELECT count(*) FROM meta.fiscal_calendar")
    distinct = one(con, "SELECT count(DISTINCT day) FROM meta.fiscal_calendar")
    assert total == distinct

    uncovered = one(
        con,
        "SELECT count(*) FROM billing.invoice_line l "
        "WHERE NOT EXISTS (SELECT 1 FROM meta.fiscal_calendar c WHERE c.day = l.invoice_date)",
    )
    assert uncovered == 0


def test_each_fiscal_year_runs_twelve_periods_of_four_four_five(
    con: duckdb.DuckDBPyConnection,
) -> None:
    for year, periods, days in con.execute(
        "SELECT fiscal_year, count(DISTINCT fiscal_period), count(*) "
        "FROM meta.fiscal_calendar GROUP BY 1 ORDER BY 1"
    ).fetchall():
        assert periods == 12, f"FY{year} has {periods} periods"
        assert days == 364, f"FY{year} spans {days} days"

    lengths = con.execute(
        "SELECT fiscal_period, count(*) / 7 FROM meta.fiscal_calendar "
        "WHERE fiscal_year = 2027 GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert [int(weeks) for _, weeks in lengths] == list(QUARTER_SHAPE * 4)
    assert FISCAL_ANCHOR.weekday() == 0, "a 4-4-5 year opens on a week boundary"


def test_fiscal_periods_and_calendar_months_agree_on_the_case(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The one that mattered. `net_revenue` declares `calendar: fiscal_445`, but
    the deck's -8% was measured on calendar months, and a 4-4-5 period is 28 or
    35 days — so the two could easily have disagreed.

    They do not: exactly one billing date falls in each fiscal period, so the
    two aggregations are the same numbers. This test is what stops that becoming
    untrue by accident when billing dates or the anchor move.
    """
    counts = con.execute(
        "SELECT c.period_label, count(DISTINCT l.invoice_date) FROM billing.invoice_line l "
        "JOIN meta.fiscal_calendar c ON c.day = l.invoice_date GROUP BY 1"
    ).fetchall()
    assert counts, "no billing dates landed in the calendar at all"
    assert {n for _, n in counts} == {1}

    calendar = con.execute(
        "SELECT strftime(invoice_date, '%Y-%m') p, sum(amount_net) FROM billing.invoice_line "
        "WHERE region = 'East' AND p IN ('2026-03', '2026-04') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    fiscal = con.execute(
        "SELECT c.period_label, sum(l.amount_net) FROM billing.invoice_line l "
        "JOIN meta.fiscal_calendar c ON c.day = l.invoice_date "
        "WHERE l.region = 'East' AND c.period_label IN ('FY2027-P01', 'FY2027-P02') "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()

    assert [round(v, 2) for _, v in calendar] == [round(v, 2) for _, v in fiscal]


def test_the_east_decline_survives_conformance(con: duckdb.DuckDBPyConnection) -> None:
    """Conformance moves six accounts between regions, so East's membership is
    not quite what the raw CSVs said. The headline had better survive that."""
    march, april = (
        v
        for _, v in con.execute(
            "SELECT strftime(invoice_date, '%Y-%m') p, sum(amount_net) "
            "FROM billing.invoice_line WHERE region = 'East' "
            "AND p IN ('2026-03', '2026-04') GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    assert (april - march) / march == pytest.approx(-0.08, abs=0.01)


def test_the_exclusion_list_must_name_accounts_that_exist(
    generated: Path, tmp_path: Path
) -> None:
    """§14.1's first filter excludes intercompany and test accounts, and the
    list is committed configuration rather than CRM data. A stale entry would
    exclude nothing while the contract still claimed to exclude something, so
    the loader refuses rather than quietly narrowing the filter to nothing."""
    stale = tmp_path / "test_accounts.csv"
    stale.write_text("account_id,reason\nACC-9999,gone\n", encoding="utf-8")

    with pytest.raises(ValueError, match="stale"):
        build(
            raw_dir=generated / "raw",
            db_path=tmp_path / "stale.duckdb",
            alias_path=ROOT / "data" / "account_alias.csv",
            test_accounts_path=stale,
        )


def test_the_watermark_is_the_max_across_all_of_a_source_s_tables(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """§22 defines it as `max(_ingested_at | _synced_at)` over the **source**,
    not over whichever of its tables happens to be checked first.

    Right now the freshest table is coincidentally listed first for all three
    sources, so a loader that read only the first would agree with one that read
    them all — and no other test could tell the difference. This computes the
    definition independently.
    """
    for source, tables in TABLES.items():
        expected = max(
            one(con, f"SELECT max({column}::TIMESTAMP) FROM {source}.{table}")
            for table, column in tables.items()
            if column is not None
        )
        rows_expected = sum(
            one(con, f"SELECT count(*) FROM {source}.{table}")
            for table, column in tables.items()
            if column is not None
        )
        stored, counted = con.execute(
            "SELECT watermark, row_count FROM meta.watermark WHERE source = ?", [source]
        ).fetchone()

        assert stored == expected, f"{source} watermark ignores one of its tables"
        assert counted == rows_expected


def test_build_accepts_an_explicit_as_of_and_stamps_it_verbatim(
    generated: Path, tmp_path: Path
) -> None:
    """`as_of` defaults to `scm.AS_OF` (every test above proves that default
    path unchanged) — this proves the additive override `data/ingest.py`
    needs actually reaches `meta.watermark.as_of`, not just `build()`'s own
    signature."""
    later = datetime(2026, 6, 1, 6, 0, 0)
    db_path = build(
        raw_dir=generated / "raw",
        db_path=tmp_path / "later.duckdb",
        alias_path=ROOT / "data" / "account_alias.csv",
        as_of=later,
    )
    con = connect(db_path)
    try:
        rows = con.execute("SELECT DISTINCT as_of FROM meta.watermark").fetchall()
        stamped = {row[0] for row in rows}
    finally:
        con.close()
    assert stamped == {later}
