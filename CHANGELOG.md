# Changelog

All notable changes to `mmemz` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet. The next release adds the entity/attribute maths itself.

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
