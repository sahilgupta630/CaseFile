"""data/ingest.py — continuous operation, piece 4 of 4.

`ingest_batch()` must never touch a byte the frozen corpus already produced —
that is the load-bearing safety property the whole module rests on (see its
own docstring and `docs/continuous-operation-plan.md`). Every test here
either proves that directly (byte-comparing the pre-existing rows before and
after), or proves the new batch is itself deterministic and ties back
correctly into `scan.py`'s own mechanism from pieces 1-3.

Each test that mutates raw CSVs works on its own fresh `generate()` output
(via the module-scoped `raw` fixture, copied per test into `tmp_path`) —
never the session-scoped `generated`/`warehouse` fixtures every other test
module shares, since `ingest_batch()` writes in place.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import duckdb
import pytest

from casefile.contract import load_all
from casefile.data import ingest, loader
from casefile.llm import StubProvider
from casefile.scan import latest_closed_period, scan_slice

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def raw(generated: Path, tmp_path: Path) -> Path:
    """A throwaway copy of the frozen corpus's raw CSVs, so a test can append
    to them without disturbing the shared session fixture other modules read."""
    copy = tmp_path / "raw"
    shutil.copytree(generated / "raw", copy)
    return copy


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


# ── next_period() ──────────────────────────────────────────────────────────────


def test_next_period_is_the_month_immediately_after_the_frozen_corpus(raw: Path) -> None:
    # The frozen corpus's own last billing month is 2026-04 (§10, AS_OF 2026-05-01).
    assert ingest.next_period(raw) == "2026-05"


def test_next_period_chains_forward_after_an_ingest(raw: Path) -> None:
    ingest.ingest_batch(raw)
    assert ingest.next_period(raw) == "2026-06"


# ── Determinism ───────────────────────────────────────────────────────────────


def test_two_independent_ingests_produce_byte_identical_new_rows(
    generated: Path, tmp_path: Path
) -> None:
    """Same convention as `test_generator.py::test_two_runs_produce_identical_bytes`
    — the same seed, on two independent copies, must append the same bytes."""
    raw_a = tmp_path / "a"
    raw_b = tmp_path / "b"
    shutil.copytree(generated / "raw", raw_a)
    shutil.copytree(generated / "raw", raw_b)

    summary_a = ingest.ingest_batch(raw_a)
    summary_b = ingest.ingest_batch(raw_b)

    assert summary_a == summary_b
    for relative in ("billing/invoice.csv", "billing/invoice_line.csv", "crm/account.csv"):
        assert (raw_a / relative).read_bytes() == (raw_b / relative).read_bytes()


# ── The past stays frozen ─────────────────────────────────────────────────────


def test_ingesting_never_changes_a_pre_existing_invoice_or_line_row(raw: Path) -> None:
    """The load-bearing regression proof: appended rows only, never a
    rewritten one — the same discipline the NORTHWIND-un-churn fix
    (`docs/DECISIONS.md`) established for the generator itself."""
    invoice_before = _read_rows(raw / "billing" / "invoice.csv")
    line_before = _read_rows(raw / "billing" / "invoice_line.csv")

    ingest.ingest_batch(raw)

    invoice_after = _read_rows(raw / "billing" / "invoice.csv")
    line_after = _read_rows(raw / "billing" / "invoice_line.csv")

    assert invoice_after[: len(invoice_before)] == invoice_before
    assert line_after[: len(line_before)] == line_before
    assert len(invoice_after) > len(invoice_before)
    assert len(line_after) > len(line_before)


def test_the_crm_resync_only_ever_touches_synced_at(raw: Path) -> None:
    before = _read_rows(raw / "crm" / "account.csv")

    ingest.ingest_batch(raw)

    after = _read_rows(raw / "crm" / "account.csv")
    assert len(after) == len(before)  # no rows added or removed
    header = before[0]
    synced_at = header.index("_synced_at")
    for old_row, new_row in zip(before[1:], after[1:], strict=True):
        assert old_row[:synced_at] == new_row[:synced_at]
        assert old_row[synced_at + 1 :] == new_row[synced_at + 1 :]
        assert old_row[synced_at] != new_row[synced_at]


# ── Churn is respected, not re-derived ────────────────────────────────────────


def test_only_accounts_billed_last_month_are_continued(raw: Path) -> None:
    """No churn logic lives in this module — an account absent from the last
    real month's invoices (because it churned, per scenario A) simply has
    nothing to continue from."""
    last_month_accounts = {
        row["account_id"]
        for row in csv.DictReader((raw / "billing" / "invoice.csv").open(encoding="utf-8"))
        if row["invoice_date"].startswith("2026-04")
    }
    summary = ingest.ingest_batch(raw)
    assert summary.accounts_continued == len(last_month_accounts)

    new_accounts = {
        row["account_id"]
        for row in csv.DictReader((raw / "billing" / "invoice.csv").open(encoding="utf-8"))
        if row["invoice_date"].startswith("2026-05")
    }
    assert new_accounts == last_month_accounts


# ── RNG independence — the NORTHWIND-un-churn regression class ───────────────


def test_different_accounts_usage_lines_get_different_noise(raw: Path) -> None:
    """A distinct gap from the test below: a stream keyed only on `(seed,
    period)` — dropping `account_id` — is still deterministic (same seed,
    same output every run) and still leaves *other* accounts' draws
    unshifted, since each `_continue_line()` call creates a fresh `random.
    Random(...)` rather than advancing one shared object. Neither of that
    class's symptoms would be caught by the tests above. What such a key
    collision actually does is give every account the *same* noise
    multiplier — caught here directly, by checking real accounts' usage-line
    price noise actually differs, not by inspecting the key format."""
    before: dict[tuple[str, str], list[float]] = {}
    for row in csv.DictReader((raw / "billing" / "invoice_line.csv").open(encoding="utf-8")):
        if row["invoice_date"].startswith("2026-04") and row["is_recurring"] == "0":
            key = (row["account_id"], row["product_id"])
            before.setdefault(key, []).append(float(row["unit_price"]))

    ingest.ingest_batch(raw)

    after: dict[tuple[str, str], list[float]] = {}
    for row in csv.DictReader((raw / "billing" / "invoice_line.csv").open(encoding="utf-8")):
        if row["invoice_date"].startswith("2026-05") and row["is_recurring"] == "0":
            key = (row["account_id"], row["product_id"])
            after.setdefault(key, []).append(float(row["unit_price"]))

    # `_continue_line()` emits exactly one new usage line per old one, in the
    # same order — so pairing each account+product group positionally (old[0]
    # with new[0], old[1] with new[1], ...) is safe, not just same-length.
    # Rounded to 3 decimals, not 6: `unit_new` is itself rounded to 2dp before
    # this ratio is taken, so a genuinely *shared* draw still shows a tight
    # spread of ratios across differently-priced accounts from rounding alone
    # (observed: a ~0.00001 spread around one shared value) — coarse rounding
    # collapses that spread back to one bucket, while real independent noise
    # (a ~4% spread) still shows up as several.
    seen_ratios = {
        round(new / old, 3)
        for key, old_list in before.items()
        for old, new in zip(old_list, after.get(key, []), strict=False)
        if old
    }

    assert len(seen_ratios) > 1, "every account got an identical noise multiplier"


def test_dropping_an_unrelated_account_does_not_change_anothers_continuation(
    raw: Path, tmp_path: Path
) -> None:
    """The same regression class `docs/DECISIONS.md` already logged once for
    the generator itself: a shared stream silently un-churning NORTHWIND when
    an unrelated account's ticket count changed. `_continue_line()`'s stream
    is keyed by `(seed, period, account_id, product_id, line_no)` — an
    account's own continuation must depend on nothing else. Proven by
    actually removing a middle account from "last month" and checking a
    *later* account's continuation is byte-identical, not by inspecting the
    key format — the same "compare full output against a clean baseline"
    discipline `docs/DECISIONS.md` used for scenario B's own stream-safety fix.
    """
    trimmed = tmp_path / "trimmed"
    shutil.copytree(raw, trimmed)

    last_month = [
        row
        for row in csv.DictReader((raw / "billing" / "invoice.csv").open(encoding="utf-8"))
        if row["invoice_date"].startswith("2026-04")
    ]
    assert len(last_month) >= 3, "need at least three billed accounts to isolate one"
    victim = last_month[len(last_month) // 2]["account_id"]
    witness = last_month[-1]["account_id"]
    assert victim != witness

    _drop_account_from_last_month(trimmed, victim)

    ingest.ingest_batch(raw)
    ingest.ingest_batch(trimmed)

    assert _new_rows_for_account(raw, witness) == _new_rows_for_account(trimmed, witness)


def _drop_account_from_last_month(raw_dir: Path, account_id: str) -> None:
    """Remove `account_id`'s April invoice + lines entirely — simulating "one
    fewer account this month" without touching anything else."""
    inv_path = raw_dir / "billing" / "invoice.csv"
    line_path = raw_dir / "billing" / "invoice_line.csv"
    inv_rows = list(csv.DictReader(inv_path.open(encoding="utf-8")))
    dropped_ids = {
        row["invoice_id"]
        for row in inv_rows
        if row["account_id"] == account_id and row["invoice_date"].startswith("2026-04")
    }
    assert dropped_ids, f"{account_id} has no April invoice to drop"

    kept_inv = [row for row in inv_rows if row["invoice_id"] not in dropped_ids]
    with inv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(kept_inv[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept_inv)

    line_rows = list(csv.DictReader(line_path.open(encoding="utf-8")))
    kept_lines = [row for row in line_rows if row["invoice_id"] not in dropped_ids]
    with line_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(kept_lines[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(kept_lines)


def _new_rows_for_account(raw_dir: Path, account_id: str) -> list[dict[str, str]]:
    rows = list(
        csv.DictReader((raw_dir / "billing" / "invoice_line.csv").open(encoding="utf-8"))
    )
    return [
        {k: v for k, v in row.items() if k != "invoice_id"}
        for row in rows
        if row["account_id"] == account_id and row["invoice_date"].startswith("2026-05")
    ]


# ── Ties back into pieces 1-3 ──────────────────────────────────────────────────


def test_ingest_then_rebuild_advances_the_watermark_scan_sees(raw: Path, tmp_path: Path) -> None:
    contracts = load_all(ROOT / "contracts")
    summary = ingest.ingest_batch(raw)
    db_path = loader.build(
        raw_dir=raw,
        db_path=tmp_path / "warehouse.duckdb",
        alias_path=ROOT / "data" / "account_alias.csv",
        as_of=summary.as_of,
    )
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        # billing and crm both advance — net_revenue and every crm-sourced
        # contract now resolve to the newly-ingested period.
        assert latest_closed_period(con, contracts["net_revenue"]) == summary.period
        assert latest_closed_period(con, contracts["gross_renewal_rate"]) == summary.period
        assert latest_closed_period(con, contracts["nrr"]) == summary.period
        assert latest_closed_period(con, contracts["expansion_arr"]) == summary.period
        assert latest_closed_period(con, contracts["new_business_arr"]) == summary.period
        # product_ops is explicitly deferred — its contract stays frozen.
        assert latest_closed_period(con, contracts["p1_resolution_time"]) == "2026-04"

        # And scan_slice() — the real run_case() path — runs cleanly against
        # the new period without raising.
        case = scan_slice(
            contracts["net_revenue"], summary.period, {"region": "East"}, con, StubProvider()
        )
        assert case.trigger.period == summary.period
    finally:
        con.close()
