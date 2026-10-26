"""mmemz — a new version of the graph content memory service.

mmemz is a from-scratch rebuild of the graph content memory service that keeps
the entity/attribute memory maths of mem0 v2.2.1 and grounds it in the proven
structures of ``memory_service_v_2.3.0`` (multi-hop graph traversal, vector
ops, and a service layer that already survives contact with real traffic).
This is version 0.1.0 and it is honestly a skeleton: the package currently
exposes its version string and the PEP 561 typing marker and nothing else.
The modules named in ``README.md`` — ``entities``, ``scoring``, ``bm25``,
``graph``, ``service``, plus the vendored ``graph_walk`` and ``vecops`` — are
planned and not yet written, so importing this package must not reach for
them, and ``__all__`` stays empty until there is something real to export.
"""

__version__ = "0.1.0"

# Deliberately empty: submodules (entities/scoring/bm25/graph/service) do not
# exist yet, and eager imports here would make `import mmemz` fail until they
# do. Keep this minimal; list names only once they are importable.
__all__: list[str] = []
