# Changelog

All notable changes to `mmemz` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — audit remediation (2026-10-26, post-audit, same 0.1.0 tree)

An independent read-only audit (`deliverables/audit-mmemz-5781a81b.md`) raised
nine findings. Every one is closed here; none changed the public API.

- **`extract()` could not parse "hired"** (`_PREDICATE_VERBS` omitted it). The
  corpus's first memory therefore stored **zero** propositions, which made the
  restatement test pass on two empty lists — a vacuous green. The verb list now
  covers the employment/commerce verbs the corpus uses, and
  `test_restating_a_fact_does_not_grow_the_memory_count` asserts the fact exists
  *before* asserting it is restated.
- **`memory_count_delta` was structurally always `0`.** It was sampled after the
  get-or-create, so it could never report the growth it causes. Now sampled
  before, with `test_memory_count_delta_can_report_growth` as a regression guard.
- **`graph_walk.py` self-report was wrong**: claimed a 20-line/3-hunk diff; the
  measured diff is **27** lines (3 hunks). Header corrected, and it no longer
  claims byte-identity — it names the dropped source line (`anchor: e1f02845`,
  authoring scratch) explicitly.
- **`vecops.py` self-report was wrong**: claimed 87 differing lines; measured
  **92**. Corrected.
- **README's mode-inversion explanation was false.** The published scores are
  exact, but the *cause* was misattributed: only `london` resolves for that
  query, so `n_matched == 1` everywhere and max-vs-sum never fires. The inversion
  comes from the per-link weight (0.5 vs 0.25) and from add-vs-multiply algebra.
  The README now says so and points at the test that exercises max-vs-sum
  separately (0.45 bounded vs 1.8 legacy).
- **README's restatement/supersede story was false**: `m5` does not collide with
  `m1`. Replaced with what the code actually does — same-id supersede is real
  (with a live transcript), and *same text under a new id still accumulates*.
- **README design rule 2 overstated the bound.** The entity arm bounds the
  *score*, not the *cost*: it still iterates a memory's links (measured ~2 µs at
  10 links, ~1.8 ms at 10 000). Rule rewritten to say exactly that.
- **Module-map and provenance claims corrected**: `scoring.py` is the entity arm
  only (no recency/importance) and imports `math`/`typing` (not `json`);
  `graph.py` has unweighted edges and no link cap; `bm25` is *not* wired into the
  service; `vecops` has no top-k helper; supersede lives in `service.consolidate`,
  not `graph.py`.
- **`mmemz/__init__.py` docstring was stale** — it said the seven modules were
  "planned and not yet written". Rewritten to list what exists and to name the
  known gaps.
- **Phase banners reordered-adjacent**: a note now explains why the methods are
  grouped by call site (write path 1→3→4, read path 1→2→5) rather than by phase
  number, so the reading order is intentional and documented.

Test count: 193 → 194 (one regression guard added).

## [0.1.0] - 2026-10-26

### Added

- Packaging skeleton for `mmemz`: `pyproject.toml` (setuptools ≥ 64,
  `setuptools.build_meta`), the `mmemz` package directory, and a PEP 561
  `py.typed` marker so the package ships as typed.
- `mmemz/__init__.py` exposing `__version__ = "0.1.0"` and an empty `__all__`.
  No submodules are imported eagerly, so `import mmemz` stays valid while the
  planned modules are still missing.
- `README.md` with the 0.1.0 module map (all planned), provenance from both
  source trees, and the design rules; `CHANGELOG.md`; `.gitignore`.

### Notes

- **Version 0.1.0 is a skeleton, not a working service.** The modules listed in
  the README — `entities`, `scoring`, `bm25`, `graph`, `service`, and the
  vendored `graph_walk` / `vecops` — are planned and do not exist yet.
- **Stdlib-only decision.** `dependencies = []`, deliberately. mmemz vendors
  the target's stdlib-only `graph_walk.py` and `vecops.py`, must remain
  droppable into `memory_service_v_2.3.0` without fighting that repo's pinned
  deps (fastapi, pydantic, qdrant, networkx, scipy, numpy), and the original
  mem0 v2.2.1 maths uses only `math` and `json` from the standard library.
  The rationale is written out at the top of `pyproject.toml`.
- Provenance: `/home/mmonk/Downloads/mem0-main(3)/mem0-main` (mem0 v2.2.1) for
  the entity/attribute maths, and
  `/home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0` for the graph
  traversal, vector ops and service structure.
