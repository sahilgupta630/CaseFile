"""Continuous operation, piece 4 of 4 — simulated incremental ingestion.

`AS_OF`/`SPAN_END` in `scm.py` are fixed module constants, so `generate()`
always produces the same frozen span — every run of `scan.latest_closed_period()`
resolves to the same "2026-04" forever. This module is the missing piece:
append one more period's worth of raw activity on top of an already-built
`data/raw/` tree, so `meta.watermark` genuinely advances across repeated calls.

**Never reconstructs `scm.World()` or calls `generator.generate()`'s `_tables()`
pipeline.** Both share one continuous RNG stream across their whole run
(`World.rng`, and `generator._tables()`'s own `rng`) — re-entering either with
bumped date constants would silently reshuffle bytes for the *already-frozen*
span, the exact bug class `docs/DECISIONS.md` already logged once (a shared
stream un-churning NORTHWIND). This module instead operates purely on the raw
CSVs already on disk, using RNG streams independently keyed by
`(seed, "ingest", period, account_id, ...)` — the same `World.stream()` idiom,
never a shared stream — so it can never touch a byte the frozen span already
produced.

**Per-source scope, deliberately split — see
`docs/continuous-operation-plan.md` design decision 3:**

* **billing** (`net_revenue`'s source) — a real, if simplified, continuation:
  every account still being billed in the last existing month gets one new
  invoice next month, recurring charges flat, usage charges lightly noised.
* **crm** (`gross_renewal_rate`/`expansion_arr`/`new_business_arr`/`nrr`'s
  source) — a trivial re-sync of `account.csv`'s own `_synced_at` column.
  `_watermarks()` only reads that column (plus `opportunity.csv`'s, untouched
  here) for this source, so this alone genuinely advances crm's watermark
  with zero new rows and zero RNG risk — the same thing `_accounts()`'s own
  docstring already says happens for real: *"rows that never change still
  arrive in [the nightly batch]."* No new renewal/opportunity business events
  are generated for the new period — stated plainly, not implied otherwise.
* **product_ops** (`p1_resolution_time`'s source) — explicitly deferred.
  Ticket arrival is a discrete process, not a continuation, and a safe
  version would touch `_propagate_tickets` — the one part of `scm.py` already
  flagged as shared-stream-fragile. `p1_resolution_time`'s own
  `latest_closed_period()` does not advance under this module.

    python -m casefile.data.ingest
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from casefile.data.scm import read_seed
from casefile.models import Base

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RAW = ROOT / "data" / "raw"

#: How late a new batch's rows are allowed to land, mirroring `generator.
#: _ingested()`'s own `low=4, high=26` hour range for billing.
_INGEST_LOW_H, _INGEST_HIGH_H = 4.0, 26.0
#: The new simulated "now" sits safely past the latest possible `_ingested_at`
#: draw above (26h) — the same ~4h buffer the committed corpus's own AS_OF
#: already keeps ahead of billing's real watermark.
_AS_OF_LAG_H = 30.0


class IngestError(LookupError):
    """No existing raw data to continue from."""


class IngestSummary(Base):
    """One `ingest_batch()` call's own receipt — lives here, not in
    `models.py`, matching `scan.ScanSummary`'s own precedent: nothing about it
    crosses the A/B/C treaty boundary `models.py` reserves for."""

    period: str
    accounts_continued: int
    invoices_written: int
    lines_written: int
    new_net_revenue: float
    as_of: datetime


def next_period(raw_dir: Path | str = DEFAULT_RAW) -> str:
    """The calendar month immediately after `invoice.csv`'s own latest
    `invoice_date` — no external state, so calling `ingest_batch()`
    repeatedly chains forward one period at a time."""
    invoice_path = Path(raw_dir) / "billing" / "invoice.csv"
    if not invoice_path.exists():
        raise IngestError(f"{invoice_path} is missing — run `make data` first")
    _, rows = _read_csv(invoice_path)
    if not rows:
        raise IngestError("billing/invoice.csv has no rows to continue from")
    latest = max(date.fromisoformat(row["invoice_date"]) for row in rows)
    following = date(latest.year + latest.month // 12, latest.month % 12 + 1, 1)
    return f"{following.year:04d}-{following.month:02d}"


def ingest_batch(raw_dir: Path | str = DEFAULT_RAW, seed: int | None = None) -> IngestSummary:
    """Append one new period of billing activity, and re-sync crm's account
    table, on top of `raw_dir`. Exactly one period per call — a caller loops
    for N. Returns the `as_of` the caller should pass to
    `loader.build(as_of=...)` when reconforming the warehouse."""
    raw_dir = Path(raw_dir)
    seed = seed if seed is not None else read_seed()
    period = next_period(raw_dir)
    year, month = (int(part) for part in period.split("-"))
    new_invoice_date = _month_end(date(year, month, 1))
    as_of = datetime.combine(new_invoice_date, time.min) + timedelta(hours=_AS_OF_LAG_H)

    accounts, invoices, lines = _continue_billing(raw_dir, seed, period, new_invoice_date)
    _resync_crm(raw_dir, seed, period, as_of)

    return IngestSummary(
        period=period,
        accounts_continued=accounts,
        invoices_written=len(invoices),
        lines_written=len(lines),
        new_net_revenue=sum(float(row[5]) for row in invoices),
        as_of=as_of,
    )


# ── billing: steady-state continuation ────────────────────────────────────────


def _continue_billing(
    raw_dir: Path, seed: int, period: str, new_invoice_date: date
) -> tuple[int, list[list[Any]], list[list[Any]]]:
    invoice_header, invoice_rows = _read_csv(raw_dir / "billing" / "invoice.csv")
    line_header, line_rows = _read_csv(raw_dir / "billing" / "invoice_line.csv")

    latest = max(date.fromisoformat(row["invoice_date"]) for row in invoice_rows)
    last_month = [
        row for row in invoice_rows
        if date.fromisoformat(row["invoice_date"]).year == latest.year
        and date.fromisoformat(row["invoice_date"]).month == latest.month
    ]
    lines_by_invoice: dict[str, list[dict[str, str]]] = {}
    for row in line_rows:
        lines_by_invoice.setdefault(row["invoice_id"], []).append(row)

    new_invoices: list[list[Any]] = []
    new_lines: list[list[Any]] = []
    for counter, old_invoice in enumerate(last_month, start=1):
        account_id = old_invoice["account_id"]
        new_invoice_id = f"ING-{period}-{counter:05d}"
        ingested = _draw_ingested(seed, period, account_id, new_invoice_date)

        gross = net = 0.0
        for line_no, old_line in enumerate(lines_by_invoice.get(old_invoice["invoice_id"], []), 1):
            new_line = _continue_line(
                seed, period, new_invoice_id, line_no, account_id, old_line,
                new_invoice_date, ingested,
            )
            new_lines.append(new_line)
            gross += float(new_line[8])
            net += float(new_line[10])

        new_invoices.append([
            new_invoice_id, account_id, new_invoice_date.isoformat(),
            old_invoice["currency"], f"{gross:.2f}", f"{net:.2f}", ingested,
        ])

    _append_csv(raw_dir / "billing" / "invoice.csv", invoice_header, new_invoices)
    _append_csv(raw_dir / "billing" / "invoice_line.csv", line_header, new_lines)
    return len(last_month), new_invoices, new_lines


def _continue_line(
    seed: int, period: str, invoice_id: str, line_no: int, account_id: str,
    old_line: dict[str, str], new_invoice_date: date, ingested: str,
) -> list[Any]:
    is_recurring = bool(int(old_line["is_recurring"]))
    qty_old = int(old_line["qty"])
    unit_old = float(old_line["unit_price"])

    if is_recurring:
        # A subscription fee doesn't drift on its own — carried forward flat,
        # matching `_billing()`'s own recurring-line math (no noise applied).
        qty_new, unit_new = qty_old, unit_old
    else:
        stream = random.Random(
            f"{seed}:ingest:{period}:{account_id}:{old_line['product_id']}:{line_no}"
        )
        qty_new = max(1, round(qty_old * stream.uniform(0.96, 1.04)))
        unit_new = round(unit_old * stream.uniform(0.98, 1.02), 2)

    gross_new = round(qty_new * unit_new, 2)
    gross_old = float(old_line["amount_gross"])
    ratio = (float(old_line["discount"]) / gross_old) if gross_old else 0.0
    discount_new = round(ratio * gross_new, 2)
    net_new = round(gross_new - discount_new, 2)
    contract_end = _safe_month_day(
        date(new_invoice_date.year + 1, new_invoice_date.month, 1), 28
    )

    return [
        invoice_id, line_no, new_invoice_date.isoformat(), account_id,
        old_line["product_id"], old_line["region"], qty_new, f"{unit_new:.2f}",
        f"{gross_new:.2f}", f"{discount_new:.2f}", f"{net_new:.2f}",
        old_line["currency"], int(is_recurring), old_line["contract_start"],
        contract_end.isoformat(), ingested,
    ]


def _draw_ingested(seed: int, period: str, account_id: str, when: date) -> str:
    stream = random.Random(f"{seed}:ingest:{period}:{account_id}:ingested")
    return (
        datetime.combine(when, time.min)
        + timedelta(hours=stream.uniform(_INGEST_LOW_H, _INGEST_HIGH_H))
    ).isoformat(timespec="seconds")


# ── crm: a trivial re-sync ─────────────────────────────────────────────────────


def _resync_crm(raw_dir: Path, seed: int, period: str, as_of: datetime) -> None:
    """Rewrite every `account.csv` row's own `_synced_at` — the last column —
    to a fresh batch-land timestamp. No other column changes; no new rows."""
    path = raw_dir / "crm" / "account.csv"
    header, rows = _read_csv(path)
    for row in rows:
        stream = random.Random(f"{seed}:ingest:{period}:crm_resync:{row['account_id']}")
        row["_synced_at"] = (
            as_of - timedelta(hours=stream.uniform(5.0, 9.0))
        ).isoformat(timespec="seconds")
    _write_csv(path, header, [[row[column] for column in header] for row in rows])


# ── CSV plumbing ────────────────────────────────────────────────────────────────


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        return fieldnames, list(reader)


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _append_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(rows)


# ── Dates ────────────────────────────────────────────────────────────────────


def _month_end(month: date) -> date:
    following = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return following - timedelta(days=1)


def _safe_month_day(month: date, day: int) -> date:
    return date(month.year, month.month, min(day, 28))


def main() -> None:  # pragma: no cover — exercised by `make ingest`, not pytest
    from casefile.data import loader

    root = ROOT
    summary = ingest_batch(root / "data" / "raw")
    loader.build(
        raw_dir=root / "data" / "raw",
        db_path=root / "data" / "casefile.duckdb",
        alias_path=root / "data" / "account_alias.csv",
        as_of=summary.as_of,
    )
    print(
        f"ingested {summary.period}: {summary.accounts_continued} accounts, "
        f"{summary.invoices_written} invoices, {summary.lines_written} lines, "
        f"₹{summary.new_net_revenue:,.0f} net revenue, as_of {summary.as_of}"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
