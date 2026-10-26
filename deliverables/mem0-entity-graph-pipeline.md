<!-- anchor: 0ff8a161 -->

# mem0's entity / attribute / graph pipeline — recognizing, injecting, extracting, consolidating, returning, reconstructing

| | |
|---|---|
| **Repo analysed** | `/home/mmonk/Downloads/mem0-main(3)/mem0-main` |
| **Version** | `mem0ai` **v2.2.1** (`pyproject.toml`) |
| **Date** | 2026-10-26 |
| **Scope** | The whole entity lifecycle: how entities and attributes are **recognized**, **injected** into the store, **extracted** by the LLM, **consolidated**, **returned** to a caller, and whether full memories can be **reconstructed** out of graph paths |
| **Evidence base** | `.shrimp/scratch/entity-pipeline-evidence.md` (all line numbers re-verified by `grep -n` against the repo above) |

## Orientation

OSS mem0 has **no graph database** — its "graph" is a *second vector collection of entity rows*, and the edges of that graph live inside the payload: each entity row carries a `linked_memory_ids` list naming the memories that mention it (`mem0/memory/main.py:559`, `:637-648`). There is no Cypher, no traversal, no typed relationship, and no `relations` key in any OSS response (`mem0/memory/main.py:1393`, `:1705-1740`). Everything downstream of that one structural decision follows: "recognition" is a spaCy NER pass rather than a graph builder, "consolidation" is an exact-or-cosine-0.95 row merge rather than an LLM reconciliation call, and "reconstructing a memory from a path" collapses into a single hop — query entity → entity rows → the `linked_memory_ids` in their payload — after which those memories are returned verbatim. The sections below walk each phase in the user's own vocabulary, and every claim carries `path:line`. The managed Platform *does* build a real graph, and the OSS/Platform split is documented in the repo; where that matters the docs are cited rather than the code.

## The six phases

| # | Phase (user's word) | What v2.2.1 does | File — function(s) |
|---|---|---|---|
| 1 | **Recognizing** the entities and attributes | spaCy `en_core_web_sm` NER plus four rule-based candidate generators, resolved by priority/confidence/span-overlap; **no LLM call**. Returns `list[(entity_type, entity_text)]`, `[]` if spaCy is absent. Query text goes through the same recogniser at search time. | `mem0/utils/entity_extraction.py` — `extract_entities` 751, `extract_entities_batch` 761, `_resolve_candidates` 705–728; `mem0/utils/spacy_models.py` — `get_nlp_full` 44, `get_nlp_lemma` 69 |
| 2 | **Injecting** into the graph | Lazily builds a *second* instance of the configured vector-store provider, collection `<collection>_entities`. One row = one entity node; payload `{data, entity_type, linked_memory_ids, user_id/agent_id/run_id}`. Upsert = embed → exact normalised-text match → else semantic match at `score >= 0.95` → append id to `linked_memory_ids`; else new `uuid4` row. Non-fatal on failure. | `mem0/memory/main.py` — `entity_store` 559, `_entity_collection_name` 422, `_upsert_entity` 605, `_existing_entities_by_text` 586, `_normalize_entity_text` 583, `_link_entities_for_memory` 707, Phase 7 of `_add_to_vector_store` 1100–1199 |
| 3 | **Extracting** memories and attributes | One LLM call in Phase 2 of a linear 8-phase `_add_to_vector_store`. Writes `text`, `attributed_to` (user/assistant, required) and an *optional* `linked_memory_ids`. `attributed_to` is stored and later promoted to top level; the LLM's `linked_memory_ids` is **never read** — memory↔memory edges do not exist in OSS. | `mem0/memory/main.py` — `add` 760, `_add_to_vector_store` 881 (phase markers 920/925/942/993/1007/1047/1100/1206); `mem0/configs/prompts.py` — `ADDITIVE_EXTRACTION_PROMPT` 468, `generate_additive_extraction_prompt` 1016–1062, output contract 920–941 |
| 4 | **Consolidating** entities and attributes | **ADD-only.** The legacy ADD/UPDATE/DELETE/NONE reconciliation prompt still ships but is unreachable. The only real consolidation is entity-row merge: same normalised text or cosine ≥ 0.95 unions `linked_memory_ids` instead of making a duplicate node; plus md5 exact-dedup of fact text and SQLite history rows. Count decay is a *scoring-time* anti-hub dampener, not a write-time merge. | `mem0/memory/main.py` — `_upsert_entity` 605 (merge 620–631), `_add_to_vector_store` Phase 4/5 1007–1045 + Phase 7d 1170–1176, `_remove_memory_from_entity_store` 652; `mem0/memory/storage.py` — `add_history` 150, `batch_add_history` 193, `get_history` 227 |
| 5 | **Returning** entities and attributes | Query-side pipeline: lemmatise + `extract_entities(query)` → embed → semantic `top_k = max(limit*4, 60)` → BM25 → entity boosts → `score_and_rank` → `MemoryItem`. Boosts need the entity rows' `linked_memory_ids` to name memory uuids, which are then fetched *as whole memories* by the normal vector query. Result is `{"results": [...]}` with the score breakdown optionally in `score_details`; **no `relations`, no graph payload**. | `mem0/memory/main.py` — `search` 1393, `_search_vector_store` 1642, `_compute_entity_boosts` 1747; `mem0/utils/scoring.py` — `score_and_rank` 60, `normalize_bm25` 43, `get_bm25_params` 16 |
| 6 | **Reconstructing** full memories from graph paths | **Does not exist in this repo.** Zero hits for `multi-hop|multi_hop|traverse|graph_path` under `mem0/ server/ cli/`. No entity→entity walk, no re-summarisation. The only "reconstruction" is one-hop rehydration of `linked_memory_ids` into verbatim memory rows. The sole triple-shaped formatter, `format_entities` (`mem0/memory/utils.py:79`), has zero callers. | *no implementation* — nearest artefacts: `mem0/memory/main.py` `_compute_entity_boosts` 1747–1825 (the one hop), `mem0/memory/utils.py` `format_entities` 79–88 (dead), `mem0/proxy/main.py:181-182` (reads `relations`, always absent for OSS) |

## §1 Code map

| Concern | File | Key symbols (line) |
|---|---|---|
| Memory engine (sync + async) | `mem0/memory/main.py` (3912 lines) | `class Memory` 487 · `add` 760 · `_add_to_vector_store` 881 · `search` 1393 · `_search_vector_store` 1642 · `_compute_entity_boosts` 1747 · `entity_store` property 559 · `_upsert_entity` 605 |
| Entity recognition (spaCy, rule-based) | `mem0/utils/entity_extraction.py` (772 lines) | `extract_entities` 751 · `extract_entities_batch` 761 · `_resolve_candidates` 705 |
| spaCy model loading (lazy, optional extra) | `mem0/utils/spacy_models.py` | `get_nlp_full` 44 (`spacy.load` 60) · `get_nlp_lemma` 69 (`spacy.load` 85) |
| Ranking math (BM25 + entity boost) | `mem0/utils/scoring.py` (~140 lines) | `score_and_rank` 60 · `normalize_bm25` 43 · `get_bm25_params` 16 |
| Prompts (extraction contract) | `mem0/configs/prompts.py` (1062 lines) | `ADDITIVE_EXTRACTION_PROMPT` 468 · `generate_additive_extraction_prompt` 1016 · `AGENT_CONTEXT_SUFFIX` 947 · legacy `DEFAULT_UPDATE_MEMORY_PROMPT` 176 |
| Storage / history (SQLite) | `mem0/memory/storage.py` (~350 lines) | `add_history` 150 · `batch_add_history` 193 · `get_history` 227 |
| Managed / hosted client (HTTP, Platform) | `mem0/client/main.py` (2313 lines) | `class MemoryClient` 227 · `add` 321 · `search` 436 (async twins 1422 / 1521) |
| OSS REST server — a *different* notion of "entity" | `server/routers/entities.py` | `list_entities` 44 — enumerates `user_id`/`agent_id`/`run_id` values across memory payloads (`SCAN_LIMIT = 10_000`), i.e. tenant ids, not graph entities |
| Docs stating the OSS / Platform split | `docs/core-concepts/how-it-works.mdx` (64), `docs/platform/features/graph-memory.mdx` | "OSS retrieval … boosts on entity overlap alone: it has no graph memory" · graph affects **ranking, not response shape**; `relations` always `[]` for compatibility; no typed, labeled relationships |

## §2 Recognizing the entities and attributes

Recognition is **spaCy-only**. `mem0/utils/entity_extraction.py` contains no prompt, no client and no
completion call: `extract_entities(text)` (`751`) does nothing but `get_nlp_full()` and
`_extract_entities_from_doc(nlp(text))` (`751-758`). The whole phase is a local NER + rules pass, so
"recognizing entities" costs a tokeniser run, not an LLM request.

| Concern | Symbol | Line |
|---|---|---|
| Single-text entry point | `extract_entities(text) -> list[tuple[str, str]]` | `entity_extraction.py:751` |
| Batched entry point | `extract_entities_batch(texts, batch_size=32)` | `entity_extraction.py:761` |
| Degradation | `if nlp is None: return []` | `entity_extraction.py:756-757` |
| Full model (NER on) | `get_nlp_full()` → lazy `spacy.load("en_core_web_sm")` | `spacy_models.py:44`, `:60` |
| Lemma model (NER off) | `get_nlp_lemma()` → `spacy.load(..., disable=["ner","parser"])` | `spacy_models.py:69`, `:85` |
| Packaging | `nlp = ["spacy>=3.7.0"]` optional extra | `pyproject.toml:28-30` |

Both entry points return `list[tuple[entity_type, entity_text]]` — **type and text, nothing else** —
and both return `[]` (or `[]` per text) when spaCy or the model is absent (`751-758`, `761-767`). spaCy
is an *optional* extra, so an install without `[nlp]` silently recognises zero entities while `add()`
still succeeds; the failure mode is an empty graph, not an error.

### The four types and the five generators

`_extract_entities_from_doc` (`731-748`) runs five generators over one `Doc` and hands the candidate
list to `_resolve_candidates` (`748`). Four entity types come out of five generators — two of them
both produce `PROPER`:

| Type | Generator | Line | What it catches |
|---|---|---|---|
| **PROPER** | `_add_ner_candidates` | `498` | spaCy NER spans (`PERSON`, `ORG`, `GPE`, …) — the model's own opinion |
| **PROPER** | `_add_proper_name_candidates` | `543` | capitalised token runs the NER tagger missed; noun-phrase heads vetted against `_GENERIC_HEADS` (`628`) |
| **IDENTIFIER** | `_add_technical_identifier_candidates` | `528` | code-shaped tokens: snake_case, dots, slashes, internal caps/digits (`_looks_like_technical_identifier` `404`, `_has_internal_cap_or_digit` `408`) |
| **QUOTED** | `_add_quoted_candidates` | `581` | anything inside quotes/backticks — the user explicitly named it |
| **TOPIC** | `_add_topic_phrase_candidates` | `590` | multi-word lower-case noun phrases, e.g. a subject being discussed |

### Resolution: one winner per text, no overlaps

`_resolve_candidates` (`705-728`) is a three-pass filter, and it is where the "graph" gets its nodes:

1. **Dedup by text** — key `_norm_text(candidate.text)` in a dict (`708`); on collision keep the better
   `(priority, -confidence)` (`709`).
2. **Drop span overlaps** (`718-724`), after sorting by `(priority, -confidence, span length, start)`
   (`712-715`). One exception (`721`): a TOPIC containing a space survives *inside* a PROPER span, so
   "machine learning" can coexist with the org/person name it sits in.
3. **Re-sort by source offset** (`726`) — output order is document order, not score order.

Consequence: for any given text there is at most one node of each type, and no two nodes share an
offset range. That is the entire "entity resolution" step.

### Precision machinery

Most of the file is blocklists and heuristics rather than NER, because `en_core_web_sm` over-fires on
generic English:

| Guard | Line | Job |
|---|---|---|
| `_GENERIC_HEADS` | `36` | blocklist of head lemmas ("system", "data", …) — a capitalised span headed by one is not an entity (`628`) |
| `_looks_like_technical_identifier` | `404` | decides if a token is code-shaped enough to be `IDENTIFIER` |
| `_has_internal_cap_or_digit` | `408` | camelCase / digit evidence used by the above |
| `_is_metric_list_context` | `416` | suppresses counts in "3 users, 4 tables" style lists |
| `_strip_trailing_metric_counts` | `422` | trims the trailing number off a span before it becomes a node |
| `_strip_generic_ending` | `367` | removes generic tails from a compound before comparison |
| `_lemmatize_compound` | `375` | lemma-form compound for text-level matching |

### Symmetry: the query goes through the same recogniser

Search is not a different code path. `_search_vector_store` calls `extract_entities(query)` verbatim
(`main.py:1649`; async twin `await asyncio.to_thread(extract_entities, query)` at `3336`), so the
entities boosted at query time are produced by exactly the rules that produced the stored nodes. A
recognition miss therefore hurts both write and read symmetrically — nothing recognises what the other
side could not.

### Attributes: there are none

Recognition yields **`(type, text)` and nothing more**. The `NamedTuple`/tuple contract at `751` and
`761` carries no attribute slot, `_EntityCandidate` resolution returns `[(entity_type, text)]`
(`729`), and no caller invents one. An attribute would have to come from somewhere the recogniser does
not look:

- the LLM extraction phase, which does write one attribute — `attributed_to ∈ {user, assistant}`
  (`prompts.py:920-941`) — but attaches it to the *memory*, not to the entity;
- or a new extractor, since the entity row payload only ever holds `data`, `entity_type`,
  `linked_memory_ids` and the identity keys (`main.py:637-648`).

So "recognizing the entities **and attributes**" in v2.2.1 is: recognise the entities, and take the
attribute from the memory's own payload instead.

## §3 Injecting the entities into the graph

"Graph" is doing a lot of work in this sentence. What v2.2.1 injects into is a **second collection in
the same vector store** — same provider, same embedding model, sibling collection name. There is no
graph engine anywhere on this path.

### A second collection, not a graph database

`entity_store` (`main.py:559-580`) is a lazily-initialised property. On first access it deep-copies
the configured `vector_store.config`, renames the collection via `_entity_collection_name` (`563`),
shares the Qdrant client in embedded mode to dodge RocksDB lock contention (`572-576`), and hands the
result to `VectorStoreFactory.create(self.config.vector_store.provider, entity_config)` (`577-579`).
So the entity store is *the same provider you already configured*, instantiated twice.

`_entity_collection_name` (`422-424`) is three lines:

```python
separator = "-" if provider == "s3_vectors" else "_"
return f"{collection_name}{separator}entities"
```

Memory vectors live in `<collection>`; entity nodes live in `<collection>_entities` (`-entities` on
`s3_vectors`). Nothing links the two collections at the storage layer — the join is done in payload,
by id.

### The node row: one entity, its type, and its edge list

| Payload field | Meaning | Single path | Batch path |
|---|---|---|---|
| `data` | the entity text exactly as the recogniser produced it | `main.py:639` | `main.py:1187` |
| `entity_type` | `PROPER` \| `QUOTED` \| `TOPIC` \| `IDENTIFIER` | `main.py:640` | `main.py:1188` |
| `linked_memory_ids` | the memory uuids this entity was seen in — **the edges** | `main.py:641` | `main.py:1189` |
| `user_id` / `agent_id` / `run_id` | whichever identity keys the call carried (`**search_filters`) | `main.py:642` | `main.py:1190` |

That is the entire schema — five fields, no `relations`, no `attributes`, no `source`/`target`.

**The node *is* the entity, and the edges are the ids inside `linked_memory_ids`.** One row is one
entity; its adjacency is a list of memory uuids in its own payload. That makes the "graph" a bipartite
entity↔memory adjacency table expressed as payload, which is why there is no traversal API to call:
you read a row's `linked_memory_ids` and fetch those memories with the ordinary vector query.
Entity↔memory is the **only** edge kind in OSS. Memory↔memory edges do not exist — the extractor's
`linked_memory_ids` proposal is never read back (`prompts.py:920-941` declares it; `main.py` never
`.get("linked_memory_ids")` on a memory payload), and entity↔entity never appears at all.

### `_upsert_entity` (`main.py:605-650`), step by step

| # | Step | Line |
|---|---|---|
| 1 | Embed the entity text: `self.embedding_model.embed(entity_text, "add")` | `608` |
| 2 | Narrow the filters to the 3 identity keys only: `{k: v for k, v in filters.items() if k in ("user_id","agent_id","run_id") and v}` | `609` |
| 3 | **Exact** match on normalised text, via `_existing_entities_by_text` | `610` |
| 4 | Else **semantic** `search(query=..., vectors=..., top_k=1, filters=...)`, accepted only at `score >= 0.95` | `614-621` |
| 5 | Match ⇒ append id, `update(vector_id=match.id, vector=None, payload=payload)`; no match ⇒ new `uuid4` row + `insert` | `623-634` / `636-648` |

Step 3 is the cheap and reliable one: `_existing_entities_by_text` (`586-603`) does a scoped
`self.entity_store.list(filters=filters, top_k=10000)` (`588-589`) and keys the rows by
`_normalize_entity_text` (`583-584`), which is only
`" ".join(value.strip().lower().split())` — case and whitespace folding, no stemming, no lemmatising.
So "Postgres"/"postgres"/" Postgres " are one node; "Postgres"/"PostgreSQL" are two unless step 4
catches them.

`update(vector=None, ...)` (`631-634`) is deliberate: the text did not change, only the edge list, so
there is no reason to spend an embedding. That choice is exactly what forces the re-embed on the
delete path below, where a payload update *does* go out with a vector.

### Phase 7 of `_add_to_vector_store` (`main.py:1100-1204`) — the batched version of the same thing

The single path runs once per memory; the batch path runs once per `add()` call over all its memories,
in five sub-steps:

| Sub-step | What it does | Line |
|---|---|---|
| **7a** global dedup | one dict keyed by normalised text, value `[entity_type, entity_text, set(memory_ids)]`, across every persisted memory — one entity per batch | `1105-1114` |
| **7b** batch embed | one `embed_batch(entity_texts, "add")`, per-text fallback to `None` on failure, then a padding/truncation guard keyed to a warning — "to avoid dropping entity links" | `1120-1145` |
| **7c** batch lookup | `entity_store.search_batch(queries=..., vectors_list=..., top_k=1, filters=...)`, plus the exact-match dict from `_existing_entities_by_text` | `1148-1157` |
| **7d** merge or queue insert | exact, else semantic at `score >= 0.95`; merge does `linked_memory_ids = sorted(set(old) \| new)` and `update(vector=None, ...)`; else queued with `sorted(memory_ids)` | `1159-1191` |
| **7e** one insert | single `entity_store.insert(vectors=..., ids=..., payloads=...)` for all new entities | `1193-1200` |

Two small asymmetries worth knowing: exact match wins unconditionally over semantic (`1167`), and the
merge is a **set union**, so ids are never removed and order is normalised by `sorted` (`1173`). A
wrong 0.95 merge is therefore permanent — nothing later splits the two entities' edge lists apart.

### Deletion keeps the adjacency honest — `_remove_memory_from_entity_store` (`652-705`)

| Step | Line |
|---|---|
| no-op if the entity store was never initialised this process | `665-666` |
| scoped `list(filters=search_filters, top_k=10000)` | `667-669` |
| strip the id; if the list empties, `delete(vector_id=row.id)` | `677-682` |
| else **re-embed** the entity text and `update(vector_id=..., vector=vec, payload=new_payload)` | `684-699` |

The re-embed exists for one reason, stated in the docstring (`655-663`): "re-embed the entity text and
update the payload (the vector store's `update()` requires a vector)". The write path can pass
`vector=None` because it uses the id it just matched; this path re-reads arbitrary rows and the store
will not accept a payload-only update there.

There are exactly **four** call sites, and `grep -n '_remove_memory_from_entity_store' main.py` returns
them plus the two definitions:

| Caller | Line (sync) | Line (async) |
|---|---|---|
| `_update_memory` (`2052` / `3770`) | `2109` | `3829` |
| `_delete_memory` (`2114` / `3834`) | `2140` | `3861` |

**`reset()` does not call it** — `reset` (`2144`) writes `self._entity_store.reset()` (`2168`) and drops
the handle (`2171`), so the whole entity collection goes without per-memory edge repair. `2210` is not a
call site either: it is the `self._entity_store = None` teardown. The async definition is at `2361`
(not a caller), body ending `2404`.

### Non-fatal by design

Every entry point on this path is wrapped in `try/except` with a log line and no re-raise:

| Site | Handler | Line |
|---|---|---|
| `_upsert_entity` whole body | `logger.warning(f"Entity upsert failed for '{entity_text}': {e}")` | `649-650` |
| Phase 7 whole block | `try:` at the phase marker, per-entity update failure at `logger.debug` | `1101`, `1180-1181` |
| `_remove_memory_from_entity_store` | per-row failures at debug, outer failure at warning | `700-705` |
| `_existing_entities_by_text` | `list()` failure falls back to `{}`, i.e. semantic dedup only | `590-592` |

The graph is therefore **best-effort decoration**. Phase 7 runs *after* Phase 6 has already persisted
the memories (`1100` vs the phase markers at `1047`), so a dead entity store, a missing spaCy model, or
a provider that rejects the `*_entities` collection produces a log line and a memory with no entity
rows — never a failed `add()`. That is a deliberate design choice, and it is also why an empty graph is
easy to mistake for a working one.

<!-- anchor: b07acb53 -->

## §4 Extracting memories and attributes

Extraction is one function, called once per `add()`. `_add_to_vector_store` (`mem0/memory/main.py:881`)
is linear: no branching between phases, no retry loop, no second model call. Everything below is a
position in that single pass.

### The eight phases

| Phase | Marker | What it does | Key lines |
|---|---|---|---|
| **0** Context gathering | `main.py:920` | builds `session_scope`, pulls the last 10 stored messages, parses the incoming turns | `920-923` |
| **1** Existing memory retrieval | `main.py:925` | embeds the parsed messages with purpose `"search"`, `vector_store.search(top_k=10)` scoped to the identity keys, then maps real UUIDs to integer strings `"0"`, `"1"`, … ("anti-hallucination") | `925-940` |
| **2** LLM extraction | `main.py:942` | **the only LLM call in the whole add path** — `self.llm.generate_response(...)` at `958`, `response_format={"type": "json_object"}` | `942-991` |
| **3** Batch embed | `main.py:993` | one `embed_batch(mem_texts, "add")`, falling back to per-text `embed()` on exception | `993-1005` |
| **4** Per-memory CPU processing + **5** Hash dedup | `main.py:1007` | md5 of the text, drop on collision with stored hashes *or* with an earlier item in the batch, lemmatise for BM25, assemble the payload | `1007-1045` |
| **6** Batch persist | `main.py:1047` | one `vector_store.insert(...)`, per-record fallback, then `batch_add_history` with `event="ADD"` | `1047-1098` |
| **7** Batch entity linking | `main.py:1100` | spaCy entities for every persisted memory → dedup → embed → merge-or-insert into the `*_entities` collection | `1100-1204` |
| **8** Save messages + return | `main.py:1206` | `db.save_messages`, build `{"id", "memory", "event": "ADD"}` rows, `capture_event("mem0.add", …)` | `1206-1230` |

Only Phase 2 touches a model that can read language. Phases 3 and 7 call the *embedding* model; Phase 7
additionally calls spaCy. Nothing in the add path ever asks an LLM to compare a new fact against an old
one — that work is absent, not deferred.

### The prompt pair

| Half | Symbol | Line | Note |
|---|---|---|---|
| System | `ADDITIVE_EXTRACTION_PROMPT` | `mem0/configs/prompts.py:468` | "Your sole operation is ADD" (`472`) |
| System suffix | `AGENT_CONTEXT_SUFFIX` | `prompts.py:947` | appended when `agent_id` set and no `user_id` — `main.py:944-946` |
| User | `generate_additive_extraction_prompt(...)` | `prompts.py:1016-1062` | called at `main.py:951-956` with `existing_memories`, `new_messages`, `last_k_messages`, `custom_instructions` |

The user prompt is nine `sections.append(...)` calls joined by blank lines (`prompts.py:1036-1062`); four are always present, `## Custom Instructions` is conditional (`1044-1045`), and two more are conditional — `## Language Requirement` (`1047-1060`) and `# Output:` (`1061`):

| Section | Line | Content |
|---|---|---|
| `## Summary` | `prompts.py:1036` | rolling conversation summary |
| `## Last k Messages` | `prompts.py:1037` | the 10 messages Phase 0 fetched |
| `## Recently Extracted Memories` | `prompts.py:1038` | what the previous turn already produced |
| `## Existing Memories` | `prompts.py:1039` | the `top_k=10` hits, as `{"id": "0", "text": …}` |
| `## New Messages` | `prompts.py:1040` | the turns being extracted |
| `## Observation Date` | `prompts.py:1041` | resolved by `_resolve_dates` (`prompts.py:1007`, called `1033`) |
| `## Current Date` | `prompts.py:1042` | same |
| `## Custom Instructions` | `prompts.py:1045` | only if `prompt` or `self.custom_instructions` is set (`main.py:948`) |

### The output contract — and where "attributes" actually are

`prompts.py:918-941`, under `# OUTPUT FORMAT` / `## Fields`:

| Field | Required? | Contract text | Line |
|---|---|---|---|
| `id` | yes | sequential integers as strings from `"0"` | `prompts.py:933` |
| `text` | yes | "contextually rich, self-contained factual statement (15-80 words)" | `prompts.py:934` |
| `attributed_to` | **yes** | `"user"` for facts stated by/about the user; `"assistant"` for information the assistant supplied | `prompts.py:935` |
| `linked_memory_ids` | no | "IDs of Existing Memories that this new memory relates to. **Use the exact IDs from the Existing Memories list.**" | `prompts.py:936` |

This is the whole attribute story. There is no schema, no key/value bag, no per-entity attribute list —
one enumerated string, `attributed_to`, and that is what §2's "there are no attributes" means in
practice. Note the shape of the required fields: everything the model emits is about *the memory*, and
exactly one field is about *who the memory belongs to*.

### What Phase 4/5 actually persists

| Step | Mechanism | Line |
|---|---|---|
| Stored-hash set | `existing_hashes` from `mem.payload.get("hash")` over the Phase 1 results | `main.py:1009-1013` |
| Batch-local set | `seen_hashes = set()` — the same text twice in one response is stored once | `main.py:1016` |
| Hash | `hashlib.md5(text.encode()).hexdigest()` | `main.py:1022` |
| Dedup test | `if mem_hash in existing_hashes or mem_hash in seen_hashes: continue` | `main.py:1023-1026` |
| BM25 copy | `text_lemmatized = lemmatize_for_bm25(text)` | `main.py:1028` |
| Payload | `data`, `text_lemmatized`, `hash`, `created_at`, `updated_at` | `main.py:1032-1037` |
| Attribute | `if mem.get("attributed_to"): mem_metadata["attributed_to"] = mem["attributed_to"]` | `main.py:1038-1039` |
| Caller metadata | `mem_metadata = deepcopy(metadata)` first, so tenant keys ride along | `main.py:1031` |

Two properties follow directly. Dedup is **exact-content only** — a paraphrase, a correction, or a
contradiction hashes differently and is stored beside the old fact. And `attributed_to` is *optional at
read time* despite being required in the contract: the `if` at `1038` silently drops an empty or missing
value rather than failing the record.

### `attributed_to` is promoted to top level on every read path

The field is written into `payload` but returned as a sibling of `id`/`memory`/`created_at`. Six sites
declare the promotion list, and each verified line is the entry `"attributed_to"` inside
`promoted_payload_keys`:

| Read path | `promoted_payload_keys` | `"attributed_to"` entry |
|---|---|---|
| sync `get` | `main.py:1238` | `main.py:1244` |
| sync `get_all` | `main.py:1356` | `main.py:1362` |
| sync `_search_vector_store` result build | `main.py:1704` | `main.py:1710` |
| async `get` | `main.py:2919` | `main.py:2925` |
| async `get_all` | `main.py:3037` | `main.py:3043` |
| async search result build | `main.py:3391` | `main.py:3397` |

`attributed_to` appears in the `core_and_promoted_keys` exclusion set at `1248` / `1365` / `1713` /
`2929` / `3046` / `3400`, so it is popped from metadata and set on the result — a field that survives
every path, unlike the one below it.

### THE LOAD-BEARING FINDING: the extractor's `linked_memory_ids` is never read

Verbatim, with real output and hit counts:

```
$ grep -n 'linked_memory_ids' mem0/memory/main.py
624:                # Update existing entity's linked_memory_ids
626:                linked_ids = payload.get("linked_memory_ids", [])
629:                    payload["linked_memory_ids"] = linked_ids
641:                    "linked_memory_ids": [memory_id],
655:        For each entity whose `linked_memory_ids` contains `memory_id`:
674:                    linked = payload.get("linked_memory_ids", [])
693:                        new_payload = {**payload, "linked_memory_ids": remaining}
1171:                            linked = set(payload.get("linked_memory_ids", []))
1173:                            payload["linked_memory_ids"] = sorted(linked)
1189:                                "linked_memory_ids": sorted(memory_ids),
1811:                        linked_memory_ids = payload.get("linked_memory_ids", [])
1812:                        if not isinstance(linked_memory_ids, list):
1815:                        num_linked = max(len(linked_memory_ids), 1)
1819:                        for memory_id in linked_memory_ids:
2313:                linked_ids = payload.get("linked_memory_ids", [])
2316:                    payload["linked_memory_ids"] = linked_ids
2328:                    "linked_memory_ids": [memory_id],
2345:        the same entity rows' linked_memory_ids lists.
2372:                    linked = payload.get("linked_memory_ids", [])
2391:                        new_payload = {**payload, "linked_memory_ids": remaining}
2851:                            linked = set(payload.get("linked_memory_ids", []))
2853:                            payload["linked_memory_ids"] = sorted(linked)
2869:                                "linked_memory_ids": sorted(memory_ids),
3489:                    linked_memory_ids = payload.get("linked_memory_ids", [])
3490:                    if not isinstance(linked_memory_ids, list):
3493:                    num_linked = max(len(linked_memory_ids), 1)
3497:                    for memory_id in linked_memory_ids:
```

**27 hits.** The narrower grep — the one that would find the code reading the field *off an extracted
memory dict*:

```
$ grep -n 'mem\.get("linked_memory_ids")\|mem\["linked_memory_ids"\]' mem0/memory/main.py
$ echo $?
1
```

**Zero hits, exit status 1.** Read the broad output honestly: every one of the 27 lines is the *entity
row's* field, not the LLM's. Lines `624-641` write `linked_memory_ids` into an entity payload during
`_upsert_entity` (`main.py:605`); `655-693` strip a memory id back out in
`_remove_memory_from_entity_store` (`main.py:652`); `1171-1189` are the batch merge union in Phase 7d;
`1811-1819` (in `_compute_entity_boosts`, `main.py:1747`) and `3489-3497` (its async twin,
`_compute_entity_boosts_async`, `main.py:3433`) are read-only consumers
for the count-decay weight; `2313-2391` and `2851-2869` are the async twins. Of the 27, twelve read the
field straight off a row (`payload.get(...)` / `payload[...]`: `626 629 674 1171 1173 1811 2313 2316 2372
2851 2853 3489`), six pull it into a local first (`1812 1815 1819 3490 3493 3497`), four write the
literal into a row (`641 1189 2328 2869`), two rebuild a row by spread (`693 2391`), two are docstring
prose (`655 2345`) and one is a comment (`624`) — 12 + 6 + 4 + 2 + 2 + 1 = 27, which is every line
`grep -n 'linked_memory_ids' main.py` returns. Not one of them touches the dict Phase 2 returned.

One more corroborating detail: the machinery that would have consumed the LLM's answer exists and is
inert. `uuid_mapping[str(idx)] = mem.id` is built at `main.py:939` (async `2622`) precisely to turn the
contract's integer ids `"0"`, `"1"` back into real UUIDs — and `grep -n uuid_mapping mem0/memory/main.py`
returns only the assignment and its initialisation (`937`, `939`, `2620`, `2622`), with no read.

**Conclusion, stated no more strongly than the evidence:** the extractor proposes a memory↔memory link
and the add path drops it on the floor. In OSS v2.2.1 the only edges that survive are the
entity↔memory ones Phase 7 derives from spaCy (`main.py:1100-1204`). The prompt asks for a graph, and
the store keeps a bipartite incidence list.

### The legacy reconciliation prompt is dead code

| Artefact | Line | Status |
|---|---|---|
| `DEFAULT_UPDATE_MEMORY_PROMPT` | `prompts.py:176` | four-op prompt (ADD / UPDATE / DELETE / NONE), still exported |
| `get_update_memory_messages` | `prompts.py:406-407` | builds the reconciliation user prompt, **no caller outside `prompts.py`** |

```
$ grep -rn get_update_memory_messages --include=*.py .
./tests/configs/test_prompts.py:4:def test_get_update_memory_messages():
./tests/configs/test_prompts.py:11:    result = prompts.get_update_memory_messages(
./tests/configs/test_prompts.py:18:    result = prompts.get_update_memory_messages(retrieved_old_memory_dict, response_content, None)
./tests/configs/test_prompts.py:22:def test_get_update_memory_messages_empty_memory():
./tests/configs/test_prompts.py:24:    result = prompts.get_update_memory_messages(
./tests/configs/test_prompts.py:32:    result = prompts.get_update_memory_messages(
./tests/configs/test_prompts.py:40:def test_get_update_memory_messages_non_empty_memory():
./tests/configs/test_prompts.py:43:    result = prompts.get_update_memory_messages(
./mem0/configs/prompts.py:406:def get_update_memory_messages(retrieved_old_memory_dict, response_content, custom_update_memory_prompt=None):
```

Nine hits, of which eight are in `tests/configs/test_prompts.py` and one is the definition
itself (`prompts.py:406`). Nothing under `mem0/memory/` calls it. It is the single strongest piece of
evidence that v2.2.1 replaced reconciliation with attribution — the code to reconcile is shipped,
tested, and unreachable.

## §5 Consolidating entities and attributes

**The blunt answer: v2.2.1's add path is ADD-only. There is no LLM reconciliation pass.** No phase in
`_add_to_vector_store` (`main.py:881-1220`) makes a *semantic* comparison of a candidate fact against an
existing one — the only comparison is exact md5 equality (Phase 5, `main.py:1009-1026`), and the one
prompt that could have (`get_update_memory_messages`, `prompts.py:406`) has no caller outside
`prompts.py` (grep above). The design is documented as a choice, not an omission —
`docs/core-concepts/how-it-works.mdx:51`:

> The automatic extraction path is additive. If a user says, "I moved from Austin to Seattle," Mem0 can
> store the new fact without silently rewriting the old one. Use explicit `update` or `delete`
> operations when your application needs to correct or remove a memory.

So "consolidation" in this codebase is four small mechanical things, none of which is semantic.

### What DOES consolidate

| # | Mechanism | Trigger | Path:line |
|---|---|---|---|
| 1 | **Exact md5 dedup** (stored + within-batch) | write time | `main.py:1009-1026` |
| 2 | **Entity-row merge** (same text, or cosine ≥ 0.95) | write time, Phase 7 | `main.py:621-631`, `1170-1176` |
| 3 | **Count decay** in the boost weight | *scoring* time, never write time | `main.py:1815-1817`, `3493-3495` |
| 4 | **Explicit maintenance** — adjacency repair + history rows + direct insert | `delete` / `delete_all` / `reset` / `update` | `main.py:652-705`, `storage.py:150/193/227`, `main.py:1960`, `1975` |

**1. Exact md5 dedup.** `hashlib.md5(text.encode()).hexdigest()` (`main.py:1022`) is checked against
`existing_hashes` (built from the 10 Phase 1 hits, `1009-1013`) and against `seen_hashes` (`1016`), which
catches the same sentence emitted twice in one response. Both are exact byte-for-byte content matches —
there is no similarity threshold, no normalisation, no stemming before hashing. Only this narrow
mechanism prevents a duplicate row.

**2. Entity-row merge — the only real graph consolidation.** `_upsert_entity` (`main.py:605`) resolves a
candidate entity in two steps and merges rather than duplicating:

| Step | Test | Line |
|---|---|---|
| exact | normalised-text dict from `_existing_entities_by_text` (def `586`) | `main.py:610` |
| semantic | `existing[0].score >= 0.95` | `main.py:621` |
| merge | `if memory_id not in linked_ids: linked_ids.append(memory_id)` then `update(vector_id=match.id, vector=None, payload=…)` | `main.py:623-631` |

The batch path (Phase 7d) does the same with a set union over the whole batch — `linked |= memory_ids`,
`payload["linked_memory_ids"] = sorted(linked)` (`main.py:1171-1173`) — else a new row is queued with
`"linked_memory_ids": sorted(memory_ids)` (`main.py:1189`). Exact match wins unconditionally over
semantic (`main.py:1167`), and ids are only ever **added to** the list. There is no subtraction on the
write path.

**3. Count decay happens at scoring time.** The code:

```python
num_linked = max(len(linked_memory_ids), 1)
memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight
```

`mem0/memory/main.py:1815-1817` (sync `_compute_entity_boosts`, `main.py:1747`), async twin at
`main.py:3493-3495`. This is an **anti-hub dampener, not a consolidation step**: it never touches a
payload, never removes an id, never merges two entities. It only deflates the score contribution of an
entity linked to very many memories — at `n=1` the weight is exactly `1.0`, at `n=10` it is `≈0.9251`, at
`n=100` it is `≈0.0926`. The adjacency list keeps every edge regardless.

**4. Explicit maintenance — the only path that ever removes an edge.** `_remove_memory_from_entity_store`
(`main.py:652`) re-reads each row, strips the id, deletes the entity row if the list empties
(`677-682`), and otherwise **re-embeds the entity text** to call `update()` (`684-699`) because the
vector store will not accept a payload-only update there (docstring `655-663`). It is called from
`delete` (`2140`, inside `_delete_memory` `2114`) and `update` (`2109`, inside `_update_memory` `2052`); async twins at `3829` / `3861`. `reset` (`2144`) does **not** call it — it wipes the whole collection via `self._entity_store.reset()` (`2168`) and drops the handle (`2171`). Alongside
it, history is written and read through `mem0/memory/storage.py` — `add_history` `150`,
`batch_add_history` `193`, `get_history` `227` — populated by Phase 6 at `main.py:1078-1089`, exposed as
`Memory.history` (`main.py:1960`), with `_create_memory` (`main.py:1975`) as the direct-insert path that
bypasses extraction entirely.

Put together: consolidation in v2.2.1 is *dedup, node-merge, and explicit deletion*. No step weighs two
statements against each other.

### What consolidating does NOT do

- **No LLM merge or contradiction resolution in the add path.** The four-op reconciler
  (`prompts.py:176`, `prompts.py:406`) is unreachable from `mem0/memory/`; `add()` only ever emits
  `event="ADD"` (`main.py:1209-1212`).
- **No UPDATE or DELETE of existing facts.** A revised fact is a new row beside the old one; the doc
  says so outright (`how-it-works.mdx:51`), and Phase 6 has no update branch (`main.py:1047-1098`).
- **No entity splitting or undo.** Merges are append-only and irreversible — Phase 7d unions ids
  (`main.py:1171-1173`) and no code path ever divides one entity row back into two, so a wrong 0.95
  match at `main.py:621` is permanent.
- **No entity→entity edges.** The only edges are entity↔memory, derived from spaCy in Phase 7
  (`main.py:1100-1204`); the LLM's memory↔memory `linked_memory_ids` is dropped (§4).
- **No attributes beyond `attributed_to`.** One enumerated field, copied through at `main.py:1038-1039`
  and promoted at `main.py:1244`; entity rows carry `data` + `entity_type` + identity keys and nothing
  else.
- **No cross-collection or global dedup outside the identity-key scope.** The md5 comparison is against
  the 10 Phase 1 hits filtered by `user_id`/`agent_id`/`run_id` (`main.py:1009`, `926`), and the entity
  lookups are scoped identically — the same fact stored under two sessions exists twice.

## §6 Returning the entities and attributes

Read time is the only place the entity graph pays for itself — everything §2–§5 built is inert until a
query arrives. And it pays off as a **ranking boost only, never as extra response payload**: the entity
rows never leave the process. `_compute_entity_boosts` (`main.py:1747-1827`) converts them into a
`{memory_id: float}` dict that is handed to `score_and_rank` (`scoring.py:60`), and what the caller
receives is the same `MemoryItem` list it would have received with the entity store switched off —
differently ordered. There is no entity field, no triple, no path in the result (§7 proves the negative).

### `_search_vector_store` — the nine steps

`def _search_vector_store(self, query, filters, limit, threshold=0.1, explain=False, show_expired=False)`
(`main.py:1642`), called from `search()` at `main.py:1508-1510` with `limit = top_k` (`main.py:1477`).
Async twin `main.py:3330`, same nine steps behind `asyncio.to_thread`.

| # | Step | Lines | What it does |
|---|---|---|---|
| 1 | Lemmatise + recognise the query | `1648-1649` (async `3335-3336`) | `query_lemmatized = lemmatize_for_bm25(query)` for BM25, then `query_entities = extract_entities(query)` — the *same* spaCy recogniser §2 describes, applied symmetrically to the query |
| 2 | Embed the query | `1652` (async `3339`) | `self.embedding_model.embed(query, "search")`; purpose `"search"` is what lets asymmetric embedders (e5-style) use a query-side prefix |
| 3 | Semantic search, over-fetched | `1655-1658` (async `3342`) | `internal_limit = max(limit * 4, 60)` then `vector_store.search(..., top_k=internal_limit)`. The multiplier is **headroom for reordering**: steps 5–8 can only reorder what step 3 returned, and a memory ranked 40th by cosine can outrank the top hit once BM25 and the entity boost land. The `60` floor protects a `top_k=1..15` caller from an empty reordering pool |
| 4 | Native keyword search | `1661-1663` (async `3348-3350`) | `vector_store.keyword_search(query=query_lemmatized, top_k=internal_limit, filters=filters)`. The **base class returns `None`**: `mem0/vector_stores/base.py:68` is the stub and `:83` is `return None`, with the docstring at `:69` stating "Returns None if not supported by this store." So BM25 silently degrades to absent rather than erroring |
| 5 | Normalise BM25 | `1666-1673` (async `3353-3360`) | `get_bm25_params(query, lemmatized=...)` (`scoring.py:16`) picks a sigmoid midpoint/steepness pair from query term count (`scoring.py:31-40`); `normalize_bm25(raw, midpoint, steepness)` (`scoring.py:43`) maps the unbounded raw score into `[0, 1]` via `1/(1+exp(-k*(x-m)))` (`scoring.py:54`). Only positive raw scores are kept (`1672`) |
| 6 | Entity boosts | `1676-1678` (async `3365-3366`) | `if query_entities: entity_boosts = self._compute_entity_boosts(query_entities, filters)` — sub-walk below. Skipped entirely when the query recognised nothing |
| 7 | Build the candidate set | `1681-1691` (async `3367-3379`) | `candidates` is built from `semantic_results` **only** — keyword results contribute a score, never a candidate. `_payload_is_expired(payload)` (`main.py:442`) drops expired rows unless `show_expired` |
| 8 | Score and rank | `1694-1701` (async `3381-3389`) | `score_and_rank(semantic_results=candidates, bm25_scores=…, entity_boosts=…, threshold=threshold, top_k=limit, explain=explain)` |
| 9 | Format into `MemoryItem` | `1704-1745` (async `3390-3431`) | Pydantic model → dict, promoted keys hoisted, remainder nested, optional `score_details` |

Two consequences worth naming. First, **the entity store can only reorder, never add**: step 7's candidate
set comes from step 3's semantic results, so an entity-boosted memory that cosine search missed entirely
is invisible — the boost has nothing to attach to. Second, **BM25 is optional at the store level and
entity boost is optional at the model level**; a store without `keyword_search` and an install without the
`[nlp]` extra turns a "hybrid" search into plain vector search with no error and no warning
(`main.py:1667`, `1677`).

### `_compute_entity_boosts` — the one hop

`main.py:1747-1827`, docstring `1748-1756`; async `_compute_entity_boosts_async` `main.py:3433-3505`.

1. **Dedup and cap the query entities** — `for entity_type, entity_text in query_entities[:8]`
   (`main.py:1761`), keyed by `self._normalize_entity_text` (`:1762`). The `[:8]` slice is where the cap
   of **8** lives; entity nine and beyond are silently never looked up. Empty list → `return {}` (`1767-1768`).
2. **Scope the filters** — `search_filters` is rebuilt from only `("user_id", "agent_id", "run_id")`
   (`main.py:1770`), so an entity boost can never cross a tenant boundary even if the caller's filter
   dict carried something wider.
3. **Batch-embed the entity texts** — `self.embedding_model.embed_batch(entity_texts, "search")`
   (`main.py:1775`), one call for up to 8 texts. A length mismatch is the one failure that is loud enough to
   abort the whole boost: `len(embeddings) != len(entity_texts)` warns and `return memory_boosts` (`1777-1783`).
4. **Search the entity collection once per entity** — `entity_store.search(query=…, vectors=…, top_k=500,
   filters=search_filters)` (`main.py:1788-1790`). **`top_k=500`** because one query entity may have been
   merged into up to 500 distinct entity rows by §5's cosine-0.95 pass. Parallelism is 4 in both twins:
   `ThreadPoolExecutor(max_workers=4)` sync (`main.py:1792`), `asyncio.Semaphore(4)` async
   (`main.py:3461`).
5. **Accept a row only at `similarity >= 0.5`** — `if similarity < 0.5: continue` (`main.py:1807`, async
   `3485`). This is a *different, looser* threshold than the 0.95 write-time merge (`main.py:621`): 0.5 is
   "close enough to boost", 0.95 is "close enough to fuse". A non-list `linked_memory_ids` is skipped
   (`1812-1813`).
6. **Weight by similarity, a global constant, and a hub dampener**:

```
num_linked         = max(len(linked_memory_ids), 1)
memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
boost              = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight
```

   `main.py:1815-1817` (async `3493-3495`), `ENTITY_BOOST_WEIGHT = 0.5` (`scoring.py:57`, imported at
   `main.py:70`). So a perfect similarity of 1.0 on a single-memory entity yields exactly `0.5`, and
   `memory_boosts[memory_key] = max(memory_boosts.get(memory_key, 0.0), boost)` (`main.py:1822`, async
   `3500`) keeps the **max across all matched entities** for a given memory — boosts never sum.

**This is the one and only hop.** Query entity → entity rows (step 4) → their `linked_memory_ids`
(step 5's payload) → memory ids that the vector query already returned (step 7 of the caller). Nothing
reads an entity row's payload for anything except `linked_memory_ids`; no entity id is used as a *query*
to find other entities; the walk stops after six lines. Everything in §7 follows from that shape.

### `score_and_rank` — where the boost becomes a number

`mem0/utils/scoring.py:60-139`.

```
has_bm25   = bool(bm25_scores)
has_entity = bool(entity_boosts)
max_possible = 1.0 + (1.0 if has_bm25 else 0.0) + (ENTITY_BOOST_WEIGHT if has_entity else 0.0)   # :97-101

semantic_score = result.get("score") or 0.0
if semantic_score < threshold:      # :111-112  ← the decisive gate
    continue
raw_combined = semantic_score + bm25_score + entity_boost                                         # :118
combined     = min(raw_combined / max_possible, 1.0)                                              # :119
```

`max_possible` is therefore `1.0`, `1.5`, `2.0` or `2.5` depending on which signals are non-empty
(docstring `scoring.py:77-81`), which is why the boost's 0.5 ceiling is *half a semantic unit* rather
than a full one.

**The decisive detail is the order of operations.** `threshold` gates the **semantic score first**
(`scoring.py:111-112`), before BM25 and the entity boost are added. The docstring says it outright
(`scoring.py:74-75`): *"Threshold gates the semantic score BEFORE combining -- candidates below the
threshold are excluded even if BM25/entity would boost them."* The consequence is counter-intuitive and
worth stating plainly: a memory that the entity graph matched at similarity 1.0, with a perfect BM25
score, is still dropped if its cosine score is 0.099 — the boost cannot rescue a candidate, only promote
one that already cleared the bar. The gate is the pre-existing vector threshold, not a hybrid threshold.

`explain=True` attaches a per-result `score_details` dict — `semantic_score`, `bm25_score`,
`entity_boost`, `raw_score`, `max_possible_score`, `final_score`, `threshold` (`scoring.py:126-135`) —
carried through at `main.py:1740-1741`. It is the only way an operator can see that an entity boost
fired at all. Sorting is `scored.sort(key=lambda x: x["score"], reverse=True)` then `[:top_k]`
(`scoring.py:138-139`).

### Result assembly — what the caller actually gets

`main.py:1704-1745`.

```python
promoted_payload_keys = ["user_id", "agent_id", "run_id", "actor_id", "role",
                         "attributed_to", "expiration_date"]                      # :1704-1712
memory_item_dict = MemoryItem(id=scored["id"], memory=payload.get("data", ""),
                              hash=payload.get("hash"), created_at=payload.get("created_at"),
                              updated_at=payload.get("updated_at"),
                              score=scored["score"]).model_dump()                 # :1722-1729
for key in promoted_payload_keys:                                             # :1731-1733
    if key in payload: memory_item_dict[key] = payload[key]
additional_metadata = {k: v for k, v in payload.items() if k not in core_and_promoted_keys}
```

`memory` is `payload["data"]` verbatim — the stored fact, not a summary of it. `attributed_to` is in
`promoted_payload_keys`, so §3's one attribute arrives as a **top-level field on each result** rather
than buried in metadata; everything not in `core_and_promoted_keys` (`main.py:1713`, which also excludes
`text_lemmatized`) is nested under `metadata` (`1735-1739`). Candidates with no `payload["data"]` are
dropped rather than returned half-formed (`1719-1720`). Note what is *absent*: no `entity`, no
`entities`, no `linked_memory_ids`, no `relations` — the entity boost survives only as the number in
`score`.

**`attributed_to` is payload-only, so it is never an input to retrieval.** It is written into the memory
payload (`main.py:1038-1039`) and promoted back out at read time, but nothing in the recognize, inject or
score path ever reads it: entity extraction runs on the memory *text* alone (`extract_entities(text)`,
`main.py:714`), and the entity rows it produces are embedded from `entity_text` alone (`main.py:608`,
`1128`), so `attributed_to` is not part of any entity's embedding, any filter the boost applies, or any
constant in the table above. Its only effect on ranking is indirect — an `assistant`-attributed fact
competes on the same semantic score as a `user`-attributed one. It labels a fact's provenance for the
caller; it does not participate in the graph.

### `search()` — the public surface

`def search(self, query, *, top_k=20, filters=None, threshold=0.1, rerank=False, explain=False,
reference_date=None, show_expired=False, **kwargs)` (`main.py:1393-1405`). It resolves filters,
calls `_search_vector_store` (`1508-1510`), optionally reranks, and returns.

```python
if rerank and self.reranker and original_memories:                            # :1514
    reranked_memories = self.reranker.rerank(query, original_memories, limit)  # :1516
...
return {"results": original_memories}                                         # :1536
```

The reranker (a `MemoryConfig` field, `configs/base.py:46-49`) runs **after** scoring and reorders the
already-boosted list; a failure is caught and the original order kept (`1518-1519`). The return value is
`{"results": [...]}` (async twin `main.py:3224`) — a dict with exactly one key.

**Stated plainly: an OSS v2.2.1 response contains no `relations` key, no entity list, and no graph
payload.** The entity graph is spent inside `score_and_rank` and does not reach the caller.

### Every constant in the read path

Verified against source, and against the config schema: `grep -rn -i "entity_boost\|boost_weight\|bm25\|top_k_multiplier\|score_threshold" mem0/configs/base.py` → **no hits**, and `MemoryConfig`
(`configs/base.py:29-57`) declares only `vector_store`, `llm`, `embedder`, `history_db_path`, `reranker`,
`version`, `custom_instructions`.

| Constant | Value | Lives at | Controls | Configurable? |
|---|---|---|---|---|
| `ENTITY_BOOST_WEIGHT` | `0.5` | `mem0/utils/scoring.py:57` (imported `main.py:70`) | Ceiling of a single-memory entity boost, and the `+0.5` added to `max_possible` (`scoring.py:101`) | **No** — module constant |
| Entity dedup cosine | `>= 0.95` | `main.py:621`, `main.py:1166` | Whether two entity rows become one node (§5) | **No** — literal in the call |
| Entity match threshold | `>= 0.5` | `main.py:1807`, `main.py:3485` | Whether a query entity's row is allowed to boost at all | **No** — literal in the loop |
| Query-entity cap | `8` | `main.py:1761`, `main.py:3437` (`query_entities[:8]`) | How many recognised query entities are looked up | **No** — slice literal |
| Boost search `top_k` | `500` | `main.py:1789`, `main.py:3469` | Entity rows harvested per query entity | **No** — keyword arg literal |
| Semantic over-fetch | `max(limit * 4, 60)` | `main.py:1655`, `main.py:3342` | Size of the reorderable pool (step 3) | **No** — literal; only the outer `limit` (= caller `top_k`, `main.py:1477`) varies |
| BM25 sigmoid params | `(5.0,0.7)` / `(7.0,0.6)` / `(9.0,0.5)` / `(10.0,0.5)` / `(12.0,0.5)` | `scoring.py:31-40` | Midpoint/steepness by query-term count (`<=3 / <=6 / <=9 / <=15 / else`) | **No** — hard-coded table |
| Final score threshold | default `0.1` | param default `main.py:1399`; forwarded `main.py:1642`; **read** at `scoring.py:111` | Semantic-score gate applied *before* combining | Partly — a `search()` **argument**, not a `MemoryConfig` field; `None` coerced to `0.1` (`main.py:1644-1645`) |
| Count decay | `1/(1+0.001*(n-1)^2)` | `main.py:1816`, `main.py:3494` | Anti-hub dampener on wide entities (`n=1`→`1.0`, `n=10`→`≈0.9251`, `n=100`→`≈0.0926`) | **No** — literal coefficients |

One correction to the dossier, since it matters for anyone tuning this: the final-score threshold is not
read at `scoring.py:121` (that line builds the result dict) — it is read at `scoring.py:111-112`, and the
0.1 itself is a `search()` signature default at `main.py:1399`, not a module constant anywhere.

## §7 Reconstructing full memories out of the graph paths

**This capability does not exist in OSS v2.2.1.** Nothing in this repository walks a graph path, and
nothing reconstructs a memory. A search returns the stored memories themselves, reordered — §6's whole
read path ends in `return {"results": original_memories}` (`main.py:1536`), and every element of that list
is a row that came out of the vector store, not a synthesis of several rows.

### The absence, as a grep

```
$ grep -rn -i "multi-hop\|multi_hop\|traverse\|graph_path" mem0/ server/ cli/
server/dashboard/pnpm-lock.yaml:1906:  estraverse@5.3.0:
server/dashboard/pnpm-lock.yaml:2302:  json-schema-traverse@0.4.1:
server/dashboard/pnpm-lock.yaml:4256:      json-schema-traverse: 0.4.1
server/dashboard/pnpm-lock.yaml:4847:      estraverse: 5.3.0
server/dashboard/pnpm-lock.yaml:4863:      estraverse: 5.3.0
server/dashboard/pnpm-lock.yaml:4920:      estraverse: 5.3.0
server/dashboard/pnpm-lock.yaml:4924:      estraverse: 5.3.0
server/dashboard/pnpm-lock.yaml:4926:  estraverse@5.3.0: {}
server/dashboard/pnpm-lock.yaml:5319:  json-schema-traverse@0.4.1: {}

```

Nine hits, and **all nine are transitive-dependency entries in the dashboard's pnpm lockfile** — two npm
packages (`estraverse`, `json-schema-traverse`) that happen to contain the substring. Restricted to
Python:

```
$ grep -rn -i "multi-hop\|multi_hop\|traverse\|graph_path" --include=*.py mem0/ server/ cli/
$ echo $?
1
```

**Zero hits, exit status 1.** No traversal primitive, no path query, no hop counter, no variable named
after a graph walk. The dossier's note that this grep returns "0 hits" is right about Python and wrong
about the raw command: the honest number is 9 hits / 0 in Python, and the 9 are lockfile noise.

### What DOES exist: one hop, then verbatim

The nearest thing to "reconstructing a memory from a path" is §6's boost, and it is a *ranking* device
that happens to read the adjacency. Restated as the only chain the code can follow:

```
query entity (spaCy, main.py:1649)
  → entity rows at similarity >= 0.5 (main.py:1788-1807)
    → row.payload["linked_memory_ids"] (main.py:1811)
      → {memory_id: boost} (main.py:1822)
        → applied to candidates the vector query ALREADY returned (main.py:1681-1691, scoring.py:116)
```

Three properties of that chain matter here.

- **The memory uuids are already in the candidate set.** `entity_boosts` is keyed by memory id and looked
  up as `entity_boosts.get(mem_id_str, 0.0)` (`scoring.py:116`) — a boost whose id is not in
  `semantic_results` is discarded. The graph does not *fetch* anything; it reweights a list it was handed.
- **Nothing walks entity→entity.** `entity_store.search` is called with an embedded *query string*
  (`main.py:1788-1790`); no code path ever takes a matched entity row's id, text, or embedding and uses it
  as a query for another entity row. The `top_k=500` in that same call is breadth *within one hop*, not a
  second hop.
- **Nothing re-summarises.** There is no LLM call anywhere in `_search_vector_store`
  (`main.py:1642-1745`) — steps 1–9 are lemmatisation, embedding, two store queries, arithmetic, and
  packaging. The memories come back **verbatim**: `memory=payload.get("data", "")` (`main.py:1724`), the
  exact string written by §3's extraction. Three memories that all mention one entity come back as three
  separate `MemoryItem`s with three separate scores — never merged into one reconstructed fact, and with
  no marker that they share an entity.

### The triple-shaped formatter with no callers

`format_entities` (`mem0/memory/utils.py:79-88`) is the only place in the tree that renders an
`source -- relationship -- destination` triple — exactly the shape a reconstructed path would be printed
in:

```python
def format_entities(entities):                                        # utils.py:79
    if not entities:
        return ""
    formatted_lines = []
    for entity in entities:
        simplified = f"{entity['source']} -- {entity['relationship']} -- {entity['destination']}"
        formatted_lines.append(simplified)
    return "\n".join(formatted_lines)
```

```
$ grep -rn "format_entities" --include=*.py .
./mem0/memory/utils.py:79:def format_entities(entities):
```

**One hit: the definition.** Zero callers, zero tests. It is a formatter for a data shape
(`{source, relationship, destination}`) that no writer in v2.2.1 produces — entity rows carry
`data`/`entity_type`/`linked_memory_ids` (`main.py:638-643`) and nothing with a `relationship` key.

### The rest of the removed graph integration

The residue is unambiguous once grepped. Each item below is a leftover from the Neo4j/Memgraph era that
`docs/platform/features/graph-memory.mdx:22` describes as superseded ("Earlier versions connected an
external graph database (Neo4j and others) and exposed a `relations` field").

```
$ grep -rn "sanitize_relationship_for_cypher\|remove_spaces_from_entities" --include=*.py . \
    | cut -d: -f1 | sort | uniq -c
      3 ./mem0/memory/utils.py
     12 ./tests/memory/test_memory_utils.py
```

The only three hits in library code are the two definitions and one internal call —
`sanitize_relationship_for_cypher` at `utils.py:253`, `remove_spaces_from_entities` at `utils.py:305`, and
`utils.py:326` wiring the second to the first. **Every other reference is in
`tests/memory/test_memory_utils.py`**, whose own test name says what it is testing:
`"""Neo4j / Memgraph path: special characters mapped via sanitize_relationship_for_cypher."""`
(`tests/memory/test_memory_utils.py:165`). A Cypher sanitiser with no Cypher caller.

```
$ grep -rn "enable_graph" mem0/
$ echo $?
1
```

**Zero hits.** The flag that gated the old integration is gone from the library entirely; it survives only
as a doc paragraph (`docs/platform/features/graph-memory.mdx:93`, "The `enable_graph` flag is no longer
needed"). Likewise the exception still advertises a dependency nothing imports —
`mem0/exceptions.py:396-397`:

```python
details={"package": "kuzu", "feature": "graph_store"},
suggestion="Please install the required dependencies: pip install kuzu"
```

A `DependencyError` example naming `kuzu` and a `graph_store` feature, in a release with no graph store.

### `relations` is read once, and that once is dead for OSS

```
$ grep -rn '"relations"' --include=*.py mem0/ server/ cli/
mem0/proxy/main.py:181:            if relevant_memories.get("relations"):
mem0/proxy/main.py:182:                entities = [entity for entity in relevant_memories["relations"]]
```

Exactly two lines, both in `_format_query_with_memories` (`mem0/proxy/main.py:175-185`):

```python
entities = []
if isinstance(self.mem0_client, mem0.memory.main.Memory):                    # :179  ← OSS branch
    memories_text = "\n".join(memory["memory"] for memory in relevant_memories["results"])
    if relevant_memories.get("relations"):                                   # :181
        entities = [entity for entity in relevant_memories["relations"]]     # :182
elif isinstance(self.mem0_client, mem0.client.main.MemoryClient):            # :183  ← Platform branch
    memories_text = "\n".join(memory["memory"] for memory in relevant_memories)
return f"- Relevant Memories/Facts: {memories_text}\n\n- Entities: {entities}\n\n- User Question: …"  # :185
```

The branch that even *looks* for the key runs only when the client is the OSS `Memory` class
(`:179`) — and OSS `search()` returns `{"results": [...]}` with one key (`main.py:1536`, `:3224`). So
`.get("relations")` is always `None` on that branch, `entities` stays `[]`, and the `- Entities:` section
of the proxy's prompt is **always empty for OSS**. The code was written for the external graph store and
was never removed when it went away: the check is not broken, it is unreachable.

### The docs state the split

`docs/core-concepts/how-it-works.mdx:64`, verbatim:

> Platform retrieval fuses these signals in the managed service, where the entity signal is powered by
> built-in [Graph Memory](/platform/features/graph-memory). OSS retrieval depends on your configured
> vector store and optional reranker, and boosts on entity overlap alone: it has no graph memory.

Same file, `:49`, on what the OSS entity store is for: entity extraction stores entities "for entity
matching at search time. On Platform these entities also become the nodes of Graph Memory."

`docs/platform/features/graph-memory.mdx` describes what the managed service adds — and even *it* refuses
the path vocabulary. `:8`: "Graph Memory is **built in**… It runs natively inside the platform and is
always on." `:39`: memories connected to query entities "receive a ranking boost, which is combined with
semantic (vector) and keyword (BM25) scores into the single `score` returned on each result." `:55`: it
"does not assign typed, labeled relationships between entities… connections are inferred from co-occurrence
rather than declared." And `:44`, verbatim:

> Graph Memory affects **ranking**, not the response shape. Search results come back in the normal format
> with a combined `score`; there is no separate graph payload to parse.

`:93` closes the loop on the key itself: "`relations` is always an empty list regardless of whether you
send it"; `:94`: "The standalone `relations` field that the external graph store returned is no longer
populated." So even on the Platform — where a real graph exists — there is nothing to reconstruct *from*:
the graph is consumed inside ranking and never serialised.

### A different notion of "entity": `server/routers/entities.py`

```
$ sed -n '14,17p;44,58p' server/routers/entities.py
SCAN_LIMIT = 10_000

EntityType = Literal["user", "agent", "run"]
TYPE_TO_FIELD: dict[EntityType, str] = {"user": "user_id", "agent": "agent_id", "run": "run_id"}
def list_entities(_auth=Depends(verify_auth)):
    buckets: dict[tuple[EntityType, str], dict[str, Any]] = defaultdict(
        lambda: {"total_memories": 0, "created_at": None, "updated_at": None}
    )

    for payload in _iter_payloads():
        created = _parse_timestamp(payload.get("created_at"))
        updated = _parse_timestamp(payload.get("updated_at")) or created

        for entity_type, field in TYPE_TO_FIELD.items():
            value = payload.get(field)
            if not value:
                continue
            bucket = buckets[(entity_type, str(value))]
            bucket["total_memories"] += 1
```

`list_entities` (`server/routers/entities.py:44`) is an admin/tenancy endpoint, not a graph endpoint. It
scans up to `SCAN_LIMIT = 10_000` memory payloads (`:14`, `:29`) and buckets them by the **identity keys**
`user_id`/`agent_id`/`run_id` (`:17`, `:53-57`), counting memories and tracking min `created_at` /
max `updated_at` per bucket (`:58-62`). What it calls an "entity" is a **tenant id**; the `Entity` model
(`:20-25`) has `id`, `type`, `total_memories`, timestamps and no mention of `entity_type`, `data`, or
`linked_memory_ids`. It never touches `entity_store`. Someone reading `GET /entities` expecting the graph's
nodes gets a list of user/agent/run ids.

### What reconstructing a memory from a path would require

Six requirements, each named against the specific thing the code lacks.

| # | Requirement | What v2.2.1 has instead |
|---|---|---|
| 1 | **Persistent entity→entity edges with a writer.** Every hop needs an edge table someone populates | No writer exists. Entity rows store two facts about one node (`data`, `entity_type`) and one adjacency list to *memories* (`main.py:638-643`); `linked_memory_ids` never contains an entity id |
| 2 | **Typed, attributed relations.** A path is only meaningful if the edges are labelled and carry provenance | The entity payload has no `relationship`, `source`, `destination`, weight, or confidence field — only `data`/`entity_type`/`linked_memory_ids`/identity keys (`main.py:638-643`), and the docs confirm no typed relationships are assigned (`graph-memory.mdx:55`) |
| 3 | **A traversal primitive over the entity collection.** A walk needs a "neighbours of this node" call | The entity store is used through exactly six operations — `search`, `search_batch`, `insert`, `update`, `delete`, `list` (`main.py:589-1196`) — all single-record, all keyed by an embedded string. There is no `neighbors()`, no `traverse()`, no hop parameter (§7 grep: zero Python hits) |
| 4 | **A synthesis step over the collected memories.** Reconstructing means composing one fact from several rows | No such call. `_search_vector_store` (`main.py:1642-1745`) contains no LLM invocation; the memories are returned verbatim via `memory=payload.get("data", "")` (`main.py:1724`). `format_entities` is the only triple formatter and has zero callers (`utils.py:79`) |
| 5 | **Memory↔memory edges resolved from the extractor's own `linked_memory_ids`.** The LLM already proposes links; a path could be built from them | Dropped: `grep 'mem\.get("linked_memory_ids")\|mem\["linked_memory_ids"\]' mem0/memory/main.py` has zero hits (§4), and the `uuid_mapping` built to resolve them (`main.py:937-939`) is written and never read. The graph stays bipartite entity↔memory |
| 6 | **A response field to carry the path.** A reconstructed memory needs somewhere to put its provenance | `search()` returns `{"results": [...]}` (`main.py:1536`, async `:3224`); `MemoryItem` (`configs/base.py:17-26`) has `id, memory, hash, metadata, score, created_at, updated_at` — no `relations`, no `entities`, no `path`. The one reader of `relations` (`proxy/main.py:181-182`) is dead for OSS |

Read together: (1)+(2) are a schema nobody writes, (3) is a primitive nobody calls, (4)+(5) are a
composition nobody performs, and (6) is a field nobody returns. That is why "reconstructing full memories
out of graph paths" is not a feature that is switched off in v2.2.1 — it is not a feature.

## §8 Gaps, what this means, and the immediate next steps

<!-- anchor: 36c013b8 -->

### Gaps

| Gap | Consequence | Where the code decides it |
|---|---|---|
| **The extractor proposes `linked_memory_ids` and nothing reads it** (§4) — the field is in the output contract | The memory↔memory edges the model is explicitly asked to produce never exist; the graph stays **strictly bipartite entity↔memory**, so no path can ever leave the memory layer | Declared `prompts.py:936`, contract `920-941`; dropped at payload assembly `main.py:1032-1039`; the resolver built for it, `uuid_mapping` (`main.py:937-939`), is written and never read |
| **The merge threshold is 0.95 cosine on `en_core_web_sm` embeddings**, and merges are **append-only** | One bad merge permanently fuses two entities' memories — and the only removal path (`_remove_memory_from_entity_store`, `main.py:652`) *unions away memory ids*, it never splits a node back apart | `_upsert_entity` merge region `main.py:621-631` (`semantic_match = existing[0] if existing and existing[0].score >= 0.95`); the batched Phase 7d form uses `matches[0]` at `main.py:1166`, unioned `1171-1173` |
| **Recognition is English-only spaCy** and `extract_entities` returns `[]` when the model is missing | The graph silently degrades to nothing on a non-English or model-less install while `add()` still succeeds — no error, no warning; the memories are stored, just unlinked (§3 "non-fatal by design") | `mem0/utils/entity_extraction.py:751-758`: `nlp = get_nlp_full()` → `if nlp is None: return []` |
| **Every boost constant is hard-coded** — cap `8` query entities, match `0.5`, weight `0.5`, `top_k=500`, dampener `1/(1+0.001*(n-1)^2)` | Tuning the graph means forking the file; none of it is reachable from `MemoryConfig` (`configs/base.py:29-57`). Enumerated in §6's constants table, which covers every boost and scoring constant (pool-sizing literals such as `max_workers=4` at `main.py:1792` are excluded) | `main.py:1761` (`[:8]`), `main.py:1807` (`>= 0.5`), `scoring.py:57` (`ENTITY_BOOST_WEIGHT`), `main.py:1789` (`top_k=500`), `main.py:1816` (dampener) |
| **No typed relations, and no attributes on entities beyond `entity_type`** (the only attribute in the system is memory-level `attributed_to`) | A "path" can only ever mean *co-occurrence*: two memories that both mention an entity. There is nothing to label an edge with and nothing to reconstruct *from* (§7, requirement 2) | Entity payload is `data` + `entity_type` + `linked_memory_ids` + identity keys only, `main.py:638-643`; no `relationship`/`source`/`destination` anywhere except the callerless `format_entities` (`utils.py:79-88`) |

### What this means

The entity layer buys exactly two things, and both are worth having: a **retrieval boost at query time**
(§6 — up to `0.5` added to a hit's *combined raw* score, `scoring.py:57`, `:101`, though the divisor caps the contribution to the returned `score` at 0.5/2.5 = `0.2` with BM25 active, `scoring.py:119`) and a **coarse co-occurrence index you
can inspect** (§3 — a second vector collection at `<collection>_entities` whose rows carry `data`,
`entity_type` and `linked_memory_ids`). It does not buy a knowledge graph. Everything §2–§7 describes is
machinery for recognising strings, embedding them, and adding a number to a ranking; the only durable
structure is a flat many-to-many between entity rows and memory ids.

"Reconstructing a full memory from graph paths" is not a missing config flag, a disabled branch, or a
feature gated behind a plan tier inside this codebase. There is **no writer of entity→entity edges**
(`linked_memory_ids` holds memory ids only, `main.py:638-643`), **no traversal primitive** (§7's grep over
`multi_hop`/`traverse`/`graph_path` is 0 hits in Python), and **no synthesis call** anywhere in
`_search_vector_store` (`main.py:1642-1745`). It is absent by construction — three primitives that were
never written — not absent by default.

The managed Platform does build the graph natively, and the repository says so plainly:
`docs/core-concepts/how-it-works.mdx:64` draws the line ("Platform retrieval fuses these signals in the
managed service… OSS retrieval… has no graph memory") and `docs/platform/features/graph-memory.mdx:8`
describes it as "built in… always on". If a real graph is the requirement, that is a **Platform feature,
not an OSS one** — and be precise about the boundary: nothing in the numbers quoted in §1–§7 is Platform
behaviour. Those are measurements of `mem0ai` 2.2.1's `Memory` class on a local vector store, and the
Platform's graph is a different implementation behind a different client (`proxy/main.py:183`), one that
consumes its graph inside ranking and never serialises it (`graph-memory.mdx:44`, `:93-94`).

### Immediate next steps

Shortest route first. Steps 1–2 cost no code changes at all; step 3 is the only one that adds a capability.

1. **Read the whole pipeline in order, in four functions.** `extract_entities`
   (`mem0/utils/entity_extraction.py:751`), `_upsert_entity` (`mem0/memory/main.py:605`), Phase 7 of
   `_add_to_vector_store` (`main.py:1100`), `_compute_entity_boosts` (`main.py:1747`). That is everything —
   recognition, write, batch write, read — and it is roughly 250 lines of source. §1–§7 of this report is
   an index to those four reads, not a substitute for them.
2. **Observe the entity store before touching it.** After a few `add()` calls, query the
   `<collection>_entities` collection directly — the name is built at `main.py:422-424` from
   `self.collection_name` (`main.py:501`) — and read one row's `linked_memory_ids` payload field
   (`main.py:638-643`). That list *is* the graph — there is nothing else to look at. No code change required.
3. **The single smallest change that creates real memory↔memory edges:** read the extractor's
   `linked_memory_ids` in Phase 4 and persist them onto the memory payload — about 30 lines, in
   `_add_to_vector_store` around `main.py:1007-1045`, with the payload assembly it feeds at `1032-1039`.
   The plumbing is already half-built: `uuid_mapping` (`main.py:937-939`) is assigned precisely to resolve
   the integer ids (`"0"`, `"1"`) the LLM was given back into real uuids, and is currently never read — so
   the resolver you need is already built and sitting ~95 lines above the drop.
4. **Make the 0.95 merge safe before trusting it** (`main.py:621-631`). Log or assert on any merge where
   the incoming `entity_text` and the matched row's `data` differ — cosine ≥ 0.95 on `en_core_web_sm` is
   not the same claim as "same entity", and the append-only merge at `:626-629` (a single-id append, never
   a removal) is irreversible.
5. **If a real graph is the requirement, stop reading OSS code.** Read
   `docs/platform/features/graph-memory.mdx` instead, and treat `mem0/proxy/main.py:181-182` as the only
   place OSS code even looks for `relations` — it is the seam where the two implementations were once the
   same one.
6. **Optional hygiene:** delete or wire up the dead paths — `format_entities`
   (`mem0/memory/utils.py:79-88`, zero callers) and `get_update_memory_messages`
   (`mem0/configs/prompts.py:406+`, unreachable per §4) — so the next reader is not misled into thinking a
   triple formatter and a reconciliation prompt imply a graph.

### Provenance

| | |
|---|---|
| **Repo path** | `/home/mmonk/Downloads/mem0-main(3)/mem0-main` |
| **Package / version** | `mem0ai` `2.2.1` — `pyproject.toml:6-7` (`name = "mem0ai"`, `version = "2.2.1"`) |
| **Report date** | 2026-10-26 |
| **Method** | Every line number in §1–§8 was read from that tree with `sed -n` / `grep -n`; greps are pasted byte-identical to live output, hit counts included (zero-hit greps show their exit status). No line number is inferred or carried over from a summary. |
| **Corrections against an earlier draft** | Three citations in this report were wrong in the draft and are fixed here: the semantic gate is read at `scoring.py:111-112` (not `:121`, which builds the result dict); the final-score threshold's `0.1` is a `search()` signature default at `main.py:1399`, not a module constant; and the multi-hop/traverse grep is 0 hits **only** with `--include=*.py` — the raw command returns 9, all lockfile noise (`estraverse`, `json-schema-traverse` in `server/dashboard/pnpm-lock.yaml`) |
| **Scope limit** | The **sync** path is documented in detail. Async twins exist and are cited only where they differ: `_compute_entity_boosts_async` `main.py:3433` (dampener `3493-3495`), `_upsert_entity_async` `main.py:2290`, `_search_vector_store` (`async def`, no `_async` suffix) `main.py:3330` (`threshold=0.1` default). Line numbers in the two families are not interchangeable. |
