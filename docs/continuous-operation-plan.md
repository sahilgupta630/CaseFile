# Continuous operation — scan, scheduler, case store, incremental ingestion

**Not one of the nine canonical docs** ([docs/README.md](README.md)) — an addendum, same
convention as [`docs/ada-integration-plan.md`](ada-integration-plan.md). Own step-ID prefix
(`CO-1`…) so it never collides with the canonical `0.x`–`6.x` ladder or `ADA-1`…`ADA-8`.

## Context

`orchestrator.run_case()`'s own `_main()` docstring admits the gap outright: *"which case to
open is not yet a solved problem in this codebase... this is the one case named on stage."*
Round 2 objective 1 ("detects and prioritises material KPI movements") and §11's own "daily
loop" narrative ([01-problem-and-solution.md](01-problem-and-solution.md)) describe exactly
this — a system that looks at every KPI x region slice on a cadence and opens the ones worth
attention. D2 explicitly allows "illustrative data, need not be production-grade," so this was
never a missing requirement, but the architecture already had the seams for it
(`contract.refresh.cadence`, per-source `meta.watermark`, a proven provisional/re-run
mechanism) without the wiring.

All four pieces (scan, scheduler, case store, incremental ingestion) are built and verified
here. Piece 4 — simulated incremental ingestion — was deliberately left unscoped when pieces
1–3 shipped, pending a decision on what "live" should actually mean with no real external
system reachable from this environment; asked directly, the answer is **simulated incremental
batches**: extend the generator/loader so new synthetic data genuinely arrives over time
(append-only), so `meta.watermark` really advances across repeated runs, rather than
`AS_OF`/`SPAN_END` staying fixed constants forever. See design decisions 11–14 below.

## Design decisions, validated against real code

1. **Region is the uniform scan dimension.** All six contracts declare `region` as
   `decomposition_dims[0]`, and exactly four regions exist corpus-wide.
   `scan.regions()` queries them directly (`SELECT DISTINCT region FROM crm.account WHERE
   region IS NOT NULL AND NOT is_test`) rather than hardcoding — the same shape
   `verify.py`'s own `_peer_expectation` already uses, plus the `is_test` exclusion
   `net_revenue.yaml`'s own formula filter already implies.

2. **`scan_slice()` is a thin wrapper over `run_case()`, not a separate `verify()` +
   `run_case()` pair.** `run_case()` already calls `verify()` first and returns immediately
   with zero LLM calls on failure. Verifying twice would double-pay `verify()`'s real cost
   (~80–100 SQL round-trips per call, dominated by materiality's 36-period history scan) for
   nothing.

3. **The case store is a separate DuckDB file — `data/casestore.duckdb` — never
   `data/casefile.duckdb`.** `loader.build()` unconditionally deletes and rebuilds the
   warehouse file on every `make data`; any table added there would be wiped on the next run.
   A separate, loader-untouched file also avoids read-only/read-write connection contention
   with everything that already opens the warehouse read-only. Already covered by the
   existing `*.duckdb` gitignore rule.

4. **Every scanned slice is persisted, not just the ones that open a case.** `run_case()` runs
   per candidate regardless, so persisting adds no cost, and it gives the audit trail the
   project's own tests already treat as a first-class property ("48 chances to cry wolf... the
   gate takes exactly three of them") — without it, nothing can answer "did we actually check
   East this month."

5. **Provider defaults to `StubProvider()`**, matching `test_cadence_upgrade.py` and
   `tools/build_real_case_fixtures.py`'s own precedent — deterministic, offline, zero cost,
   safe for CI and the replay demo. `provider_from_env()` stays available as an explicit
   `--live` opt-in on the CLI, mirroring how `orchestrator._main()` already fails loudly, not
   silently, on a live-provider gap.

6. **The scheduler is real, tested, in-process code — `run_scheduled()`** — "a Python function
   on a timer," explicitly not Airflow/Kafka/a daemon framework. Injectable `sleep` and an
   iteration cap make it unit-testable without a real wait. An OS-level crontab/systemd-timer
   example is the documented *production* answer (below), not built or tested here —
   environment-specific, out of scope for this repo.

7. **The frozen corpus can't demonstrate "time advancing" by itself.** `AS_OF`/`SPAN_END` are
   fixed constants baked into the seed, so `latest_closed_period()` resolves to the same
   period every tick against the stock warehouse. The demonstration instead comes from
   `tools/replay_scan.py` walking the corpus's own already-existing trailing periods, and from
   reusing `test_cadence_upgrade.py`'s exact watermark-mutation technique for the
   provisional-upgrade proof. Nobody should expect the scheduler to show something new against
   the static seed alone.

8. **Ground truth for `scan.py`'s own end-to-end test is `test_verify.py`, not
   `test_materiality.py`.** `test_materiality.py`'s own full-corpus test proves 4 slices pass
   *materiality alone* (pre-`verify()`, net_revenue only). The number that matches what
   `scan()` actually does (full `run_case()` → full `verify()` per slice) is
   `tests/test_verify.py::test_exactly_one_movement_in_the_whole_corpus_survives_verify`:
   across the 4-region × trailing-12-period grid, **exactly one survives** (East/2026-04), and
   three of the four materiality-passing candidates close with named reasons. Reproducing this
   through `scan_slice()` (real `run_case()` cost, not `verify()` alone) is
   `tests/test_scan.py::test_the_slice_level_backtest_reproduces_verifys_own_survivors` — the
   real proof this module does not change Stage 1's own established outcome. It pre-filters by
   materiality first, the same optimisation `test_verify.py`'s own test already uses, so the
   heavier `run_case()` cost is paid only for the 4 slices that need it, not all 48.

9. **`casestore.list_cases(open_only=...)`** reads `verification.passed` — `Case` has no
   separate status field, and this is the one existing boolean the rest of the test suite
   already treats as "worth attention" versus "closed for a documented reason." The flat
   `provisional`/`confidence`/`kpi`/`period`/`dimensions`/`priority` columns exist for the
   same reason (a lightweight query without deserialising `payload`) and are checked directly
   in `tests/test_casestore.py::test_the_flat_columns_match_the_cases_own_fields` — the one
   place a mistake in `save()`'s column mapping for them would be caught at all.

10. **A real gap `scan()` found, not assumed — 7 of 24 real slices cannot even be verified.**
    Every existing test in this repository picks a known-good contract/period/region by hand;
    `scan()` is the first thing here that sweeps every contract against every region blindly.
    Measured against the real committed warehouse (all six contracts, each at its own
    `latest_closed_period()`, all four regions): `verify()` itself raises before returning a
    result for 7 of the 24 resulting slices —

    - `_movement()`'s own `VerifyError` ("no previous period to compare against") for a ratio
      or ARR-flow KPI with nothing due in one of the two compared periods for that region
      (`gross_renewal_rate`/APAC, `new_business_arr`/APAC, `nrr`/APAC), and
    - a bare `ValueError` from `stats/stl.py` ("STL needs two full cycles") for a region whose
      *own* value series has fewer non-null observations than `verify.py`'s
      `_history_is_sparse()` assumes — that check reads the calendar span between
      `contract.history_start` and the period end, not the count of actually-defined values,
      and a region with gaps (no renewals due some months) can have a long enough calendar
      span but still too few real observations for STL's own two-cycle requirement
      (`gross_renewal_rate`/North and West, `nrr`/North and West).

    Both are pre-existing Stage 1 gaps, not introduced by this module — out of scope to fix
    here: touching `verify.py`'s sparse-history detection risks shifting the materiality
    false-alarm calibration already measured and committed in
    [06-quality.md §35.6](06-quality.md) (`docs/ada-integration-plan.md`'s ADA-2). `scan()`
    treats either as "not scannable this period," not a crash — counted in
    `ScanSummary.slices_unverifiable`, the sweep continues — the same "closing early is a
    success path" principle `run_case()` already applies one level up, extended one step
    further to "not opening at all is also not a crash." Logged here rather than fixed,
    matching the treatment `docs/ada-integration-plan.md`'s own out-of-scope findings and
    `docs/DECISIONS.md`'s "Scenario G attempted and deferred" entry already got.

Both open questions from the validation pass that shipped pieces 1–3 were put to the user
directly and confirmed: **all six contracts per scan** (not narrowed to `net_revenue` first —
decision 10 above is a direct, measured consequence of that choice), and **real
`run_scheduled()` code**, not documentation-only.

11. **Piece 4 never reconstructs `scm.World()` or calls `generator.generate()`'s `_tables()`
    pipeline.** Both share one continuous RNG stream across their whole run (`World.rng`
    inside `_build_accounts`/`_propagate_tickets`, and `generator._tables()`'s own `rng`) —
    re-entering either with bumped date constants would silently reshuffle bytes for the
    *already-frozen* span, the exact bug class `docs/DECISIONS.md` already logged once (a
    shared stream un-churning NORTHWIND). `data/ingest.py` instead operates purely on the raw
    CSVs already on disk, via the stdlib `csv` module, using RNG streams independently keyed
    by `(seed, "ingest", period, account_id, ...)` — the same `World.stream()` idiom, never a
    shared stream. This is the load-bearing safety property the whole module rests on, and it
    is what makes the module additive: `generate()`'s "same seed, same bytes" contract
    (`test_two_runs_produce_identical_bytes`) is a self-consistency check between two fresh
    runs, not a pinned golden hash, so a module that never touches `generate()`'s own default
    call path cannot break it — proven directly by `tests/test_ingest.py::
    test_ingesting_never_changes_a_pre_existing_invoice_or_line_row` byte-comparing every
    pre-existing row before and after.

12. **Per-source scope, deliberately split by what's safe and cheap versus what would need new
    RNG-sensitive business-event generation** — billing (`net_revenue`'s source) gets a real,
    if simplified, steady-state invoice continuation (recurring charges flat, usage charges
    lightly noised via an independently-keyed stream); crm
    (`gross_renewal_rate`/`expansion_arr`/`new_business_arr`/`nrr`'s source) gets a trivial
    re-sync of `account.csv`'s own `_synced_at` column, since `_watermarks()` only reads that
    column (plus `opportunity.csv`'s, untouched) for this source — zero new rows, zero RNG
    risk, and it mirrors `_accounts()`'s own existing docstring verbatim ("rows that never
    change still arrive in it"); product_ops (`p1_resolution_time`'s source) is explicitly
    deferred, since ticket arrival is a discrete process, not a continuation, and a safe
    version would touch `_propagate_tickets` — the one part of `scm.py` already flagged above
    as shared-stream-fragile. Measured, not assumed: `p1_resolution_time`'s own
    `latest_closed_period()` genuinely stays at `2026-04` after ingestion while the other five
    contracts advance — `tests/test_ingest.py::
    test_ingest_then_rebuild_advances_the_watermark_scan_sees` checks both halves of that
    directly. Logged plainly as a real, honest limitation — the same "cheap to cut, deferred,
    logged" treatment `docs/ada-integration-plan.md`'s ADA-7/8 and Scenario G already got.

13. **`loader.py` gets one small, additive, backward-compatible change:**
    `build(..., as_of: datetime | None = None)`, threaded into `_watermarks(con, as_of)`,
    defaulting to `scm.AS_OF` when omitted — every caller written before this parameter
    existed (`scan.py`, every test, `tools/build_real_case_fixtures.py`, `make data`) passes
    nothing and is completely unaffected; `tests/test_loader.py::
    test_build_accepts_an_explicit_as_of_and_stamps_it_verbatim` proves the override path,
    and every pre-existing `test_loader.py` assertion proves the default path untouched.

14. **A real gap the mutation-testing pass found, not assumed clean: an RNG key missing
    `account_id` is invisible to both a plain determinism check and a "does dropping an
    account shift another's output" check.** Because `_continue_line()` builds a fresh
    `random.Random(...)` object per call rather than advancing one shared object across a
    loop, a badly-scoped key (e.g. `f"{seed}:ingest:{period}"` alone) is still fully
    deterministic run-to-run, and dropping an unrelated account doesn't shift anyone else's
    stream position — it just silently gives every account the *identical* noise multiplier,
    a different symptom neither of those tests was built to catch. Caught by a dedicated third
    test, `tests/test_ingest.py::test_different_accounts_usage_lines_get_different_noise`,
    which compares the actual price-noise ratio applied across different real accounts rather
    than inspecting the key format — and confirmed the other way too: the first version of
    that test rounded ratios to 6 decimal places and still saw "variation" under the buggy key,
    because `unit_new`'s own 2dp rounding produces a small ratio spread purely from different
    accounts' unit prices even when the underlying random draw is identical; rounding to 3dp
    fixed the false negative. Both the original mutation (missing `account_id`) and the test's
    own false-negative were found and fixed before this was counted done.

## Files — done, verified

581 backend tests pass (`pytest -q`, up from 552 before this initiative), `ruff check src
tests` and `mypy src` both clean, `tools/check_ground_truth_isolation.py` and
`tools/check_links.py` both clean. Every load-bearing piece of logic was mutation-tested by
hand before being counted done (a real bug introduced on purpose, confirmed caught by a
failing test, then reverted) — see `docs/DECISIONS.md` for specifics, including two real
test-coverage gaps the mutation pass itself found and closed (pieces 1–3's case-store flat
columns had no test reading them back at all; piece 4's first RNG-independence test had a
rounding-precision false negative).

| # | Piece | Files | Verify | Status |
|---|---|---|---|---|
| CO-1 | Scan — `latest_closed_period`, `regions`, `scan_slice`, `scan` | `src/casefile/scan.py` (new) | `tests/test_scan.py`: period/region lookups (incl. the `>=` month-end boundary), the all-six-contracts sweep (decision 10), the `gate1`-marked slice-level backtest against `test_verify.py`'s own survivors | ✅ |
| CO-2 | Scheduler — `run_scheduled` | `src/casefile/scan.py` | `tests/test_scan.py`: `iterations=3` with a fake `sleep`, `scan()` itself replaced via `monkeypatch` — zero real wait, zero real `run_case()` cost | ✅ |
| CO-3 | Case store | `src/casefile/casestore.py` (new) | `tests/test_casestore.py`: round-trip save/load, upsert-not-duplicate, `open_only` filter, priority ordering, flat-column integrity; `tests/test_scan.py`'s cadence-upgrade test proves the store upserts the same `case_id` in place when a provisional case's ceiling lifts (reusing `test_cadence_upgrade.py`'s own watermark-mutation technique) | ✅ |
| CO-4 | Demo-facing proof (frozen periods) | `tools/replay_scan.py` (new), `Makefile` (`scan`/`replay` targets) | `python tools/replay_scan.py` / `make replay` walks 2026-02 → 2026-04 against a fresh generated corpus, printing a §11-shaped transcript; `make scan` runs against the committed warehouse and populates `data/casestore.duckdb` | ✅ |
| CO-5 | Piece 4 — simulated incremental ingestion | `src/casefile/data/ingest.py` (new) | `tests/test_ingest.py` (9 tests): `next_period()`/chaining, byte-frozen past, churn respected not re-derived, RNG independence (decision 14), end-to-end tie-back into `scan.latest_closed_period()` | ✅ |
| CO-6 | `loader.build()`'s additive `as_of` parameter | `src/casefile/data/loader.py` | `tests/test_loader.py::test_build_accepts_an_explicit_as_of_and_stamps_it_verbatim` plus every pre-existing test in that file (default path unchanged) | ✅ |
| CO-7 | Demo-facing proof (new periods) | `tools/replay_scan.py` (extended), `Makefile` (`ingest` target) | `python tools/replay_scan.py` / `make replay` now also ingests and scans two new periods after the frozen walk; `make ingest` (no `data` dependency, so repeated calls chain) runs against the committed warehouse | ✅ |

## The OS-level production answer (documented, not built)

`run_scheduled()` proves the loop's own logic; the actual production deployment is an
OS-level timer calling the module's CLI entrypoint on each contract's own `refresh.cadence`,
e.g.:

```cron
# billing/crm refresh daily — run the sweep once after each day's batch lands
0 7 * * *  cd /opt/casefile && python -m casefile.scan --live >> /var/log/casefile-scan.log 2>&1
```

or the equivalent `systemd` timer unit calling the same command. This is genuinely
environment-specific (which host, which secrets manager for the live API key, log rotation)
and not something this repository's own test suite can prove — stated here as the documented
answer rather than asserted as done.

## Verification

1. `pytest -q`, `ruff check src tests`, `mypy src` — same bar as every change this session. ✅
2. `tools/check_ground_truth_isolation.py`, `tools/check_links.py` — same bar `make check`
   holds every change to. ✅
3. `python tools/replay_scan.py` runs end to end against a fresh `generate()`+`build()` corpus,
   walking 3 frozen periods then ingesting and scanning 2 newly-arrived ones, and produces a
   real, inspectable transcript. ✅
4. `python -m casefile.scan` (`make scan`) runs against the committed `data/casefile.duckdb`,
   populates `data/casestore.duckdb`, and prints a `ScanSummary`. ✅
5. `python -m casefile.data.ingest` (`make ingest`), run twice in a row against the committed
   corpus with no `data` in between, genuinely chains forward two periods (measured: `2026-05`
   then `2026-06`) and prints an `IngestSummary` each time. ✅

Not committed or pushed — per standing instruction, the exact diff is shown for review first.
