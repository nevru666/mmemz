"""mmemz — a new version of the graph content memory service.

mmemz is a from-scratch rebuild of the graph content memory service that keeps
the entity/attribute memory maths of mem0 v2.2.1 and grounds it in the proven
structures of ``memory_service_v_2.3.0`` (multi-hop graph traversal, vector
ops, and a service layer that already survives contact with real traffic).
This is version 0.1.0.

What actually exists at 0.1.0, and what is deliberately missing:

* ``mmemz.entities``   — normalisation, type decision, dedupe at 0.82, propositions
* ``mmemz.scoring``    — bounded + legacy entity-relevance arms (the maths)
* ``mmemz.bm25``       — BM25 sigmoid normaliser; OPTIONAL and not wired into
                         the service (``is_used_by_service()`` is ``False``)
* ``mmemz.graph``      — the bipartite memory/entity store
* ``mmemz.service``    — the six phases and the search/remember/forget API
* ``mmemz.graph_walk`` — vendored bounded multi-hop BFS (header-only diff)
* ``mmemz.vecops``     — vendored vector helpers, numpy branches removed

Known gaps, stated so they are not mistaken for features: ``extract()`` is a
small lexical splitter, not an LLM call, and returns nothing for a verb it does
not know; the entity arm bounds its *score* per memory but not the *work*, so it
still iterates a memory's links; and the BM25 module is unused. See README.md.

``__all__`` stays empty on purpose: this package is imported by path for its
submodules, and an eager re-export here would make ``import mmemz`` pay for all
of them (and would couple the version string to their import health).
"""

__version__ = "0.1.0"

__all__: list[str] = []
