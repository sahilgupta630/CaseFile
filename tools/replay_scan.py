"""The demo-facing proof that continuous operation's pieces 1-4 work end to
end (`docs/continuous-operation-plan.md`).

Mirrors `tools/build_real_case_fixtures.py`'s own `generate()` +
`build()`-into-a-tempdir pattern. Two phases:

1. Walks the corpus's own already-existing trailing periods one at a time,
   calling `scan()` per period against a tempdir case store, printing a
   transcript in the shape of §11's "daily loop" narrative
   (`docs/01-problem-and-solution.md`). The frozen corpus cannot demonstrate
   time actually advancing by itself — `AS_OF`/`SPAN_END` are fixed constants
   baked into the seed — so this walks the corpus's own real trailing months.
2. Calls `data.ingest.ingest_batch()` twice, rebuilding the same warehouse
   with each batch's own new `as_of`, and scans each newly-arrived period too
   — the piece the frozen replay above cannot show by itself.

    python tools/replay_scan.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb

from casefile import casestore
from casefile.contract import load_all
from casefile.data import ingest
from casefile.data.generator import generate
from casefile.data.loader import build
from casefile.llm import StubProvider
from casefile.models import KPIContract
from casefile.scan import scan

ROOT = Path(__file__).resolve().parents[1]

#: Three of the corpus's own real trailing months — enough to show the
#: mechanism (a scan that opens nothing, one that opens several, sweeping all
#: six contracts each time) without paying a full 12-month replay's cost.
PERIODS = ["2026-02", "2026-03", "2026-04"]

#: How many new periods to simulate arriving after the frozen walk above.
INGESTED_BATCHES = 2


def main() -> None:
    contracts = load_all(ROOT / "contracts")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        raw_dir = tmp_path / "corpus" / "raw"
        db_path = tmp_path / "warehouse" / "casefile.duckdb"
        alias_path = ROOT / "data" / "account_alias.csv"

        generate(tmp_path / "corpus")
        build(raw_dir=raw_dir, db_path=db_path, alias_path=alias_path)
        store = casestore.connect(tmp_path / "casestore.duckdb")
        try:
            con = duckdb.connect(str(db_path), read_only=True)
            try:
                for period in PERIODS:
                    _replay_one_period(contracts, con, store, period)
            finally:
                con.close()

            print("-- new data arrives --")
            for _ in range(INGESTED_BATCHES):
                summary = ingest.ingest_batch(raw_dir)
                build(raw_dir=raw_dir, db_path=db_path, alias_path=alias_path, as_of=summary.as_of)
                con = duckdb.connect(str(db_path), read_only=True)
                try:
                    _replay_one_period(contracts, con, store, summary.period)
                finally:
                    con.close()
        finally:
            store.close()


def _replay_one_period(
    contracts: dict[str, KPIContract],
    con: duckdb.DuckDBPyConnection,
    store: duckdb.DuckDBPyConnection,
    period: str,
) -> None:
    summary = scan(contracts, con, StubProvider(), period=period, store=store)
    print(
        f"{period}  scanned {summary.slices_checked} slices "
        f"({len(contracts)} KPIs x 4 regions) — {summary.cases_opened} opened, "
        f"{summary.slices_unverifiable} unverifiable, {summary.model_calls} model calls, "
        f"{summary.wall_ms:,.0f}ms"
    )
    opened = [
        case
        for case in casestore.list_cases(store, open_only=True)
        if case.trigger.period == period
    ]
    for case in opened:
        verdict = case.verdict
        primary = next((a for a in verdict.attribution if a.status == "primary"), None) \
            if verdict is not None else None
        detail = f"{verdict.confidence}" if verdict is not None else "?"
        if primary is not None:
            detail += f", primary driver {primary.driver_id}"
        print(f"    -> {case.id}: {detail}")


if __name__ == "__main__":
    main()
