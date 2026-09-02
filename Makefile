# §41.3 — the commands every verification in §44 refers to.
#
# `make check` is the contract with CI: it runs exactly what ci.yml runs, in the
# same order. If those two ever disagree, fixing the divergence outranks every
# other task in the project.

.PHONY: setup data corpus demo scan replay ingest test check gate0 gate1 gate2 gate3 gate4 gate5 gate6

setup:
	pip install -e ".[dev]"

# Deterministic by construction: same seed, same bytes. The manifest of sha256
# digests it writes is what ladder step 0.7 is verified against. The loader then
# conforms the three sources into DuckDB and records a watermark for each.
data:
	python -m casefile.data.generator
	python -m casefile.data.loader

# §41.3 calls this "rare; needs an API key". It needs neither: §24's fourth
# control is that the noise is templates and only the signal is authored, so the
# corpus regenerates from the seed like everything else. The authored documents
# under data/corpus/authored/ are committed; this validates that each one still
# attaches to something retrievable, and reports the noise floor.
corpus:
	python -m casefile.data.generator
	python -m casefile.data.corpus

# §10's own worked example: Net Revenue, East, 2026-04. Depends on `data` so
# the warehouse is never stale — regeneration is fast and deterministic (same
# seed, same bytes). Needs a recorded response in llm_cache/ for this exact
# prompt, or CASEFILE_LLM_REPLAY=false plus a real ANTHROPIC_API_KEY; orchestrator.py
# fails loudly, not silently, when neither is available.
demo: data
	python -m casefile.orchestrator

# Continuous operation (docs/continuous-operation-plan.md). Sweeps every
# contract x region slice over the committed warehouse's own latest closed
# period, writing data/casestore.duckdb. StubProvider by default; pass
# --live for a real provider via provider_from_env().
scan: data
	python -m casefile.scan

# The same mechanism, replayed against the corpus's own last three real
# trailing months, then two simulated newly-arrived ones (piece 4) — the
# demo-facing proof all four pieces work end to end in a tempdir case store.
replay: data
	python tools/replay_scan.py

# Piece 4: append one new period of billing activity (and re-sync crm's
# account table) on top of data/raw/, then rebuild data/casefile.duckdb with
# the new watermark. Deliberately does NOT depend on `data` — unlike scan/
# replay/demo, ingest is meant to be re-run repeatedly, each call chaining
# one further period onto whatever is already there; depending on `data`
# would reset raw/ back to the frozen baseline before every call and undo
# the chaining. Run `make data` once first on a fresh clone. See
# data/ingest.py.
ingest:
	python -m casefile.data.ingest

test:
	pytest -q

# In ci.yml's order. The two tools/ checks were missing here, which is how a
# ground-truth violation reached CI at ladder step 0.7 with `make check` green —
# precisely the divergence §41.3 says outranks every other task. The ui job
# joined ci.yml at 1.9; its two commands join here for the same reason.
check:
	python tools/check_ground_truth_isolation.py
	python tools/check_links.py
	ruff check src tests
	mypy src
	pytest -q
	cd ui && npm ci && npm test && npm run build

gate0 gate1 gate2 gate3 gate4 gate5 gate6:
	pytest -m $@ -q
