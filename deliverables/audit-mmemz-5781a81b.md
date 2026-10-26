<!-- anchor: 5781a81b -->
# Independent audit — mmemz 0.1.0

Target: `/home/mmonk/Downloads/LSTM_VS_LLM/mmemz` (`mmemz/`, `tests/`). READ-ONLY audit.
**DEVIATION FROM BRIEF:** the harness denied every write to `/tmp` (`fs_scope: /home/mmonk/Downloads/LSTM_VS_LLM/mmemz`), so the mutation copies were made in the pinned artifacts dir
`.shrimp/contexts/goal-extract-mem0-v2-2-1-s-maths-and-cod-ad1580e3/5781a81b/artifacts/` (a `cp -r` of `mmemz tests pyproject.toml README.md`). The real tree was never edited; it was re-verified afterwards (`Ran 193 tests ... OK`).

NOT READ (negative read-list, decided before first read): `JOURNAL.md`, `DELIVERABLES.md`, `mem0_scoring.py`, `mmemz_entity_boost_patch.py`, `sessions/`, `.shrimp/contexts/*` other than my own artifacts dir. Prior narrative is not evidence.

---

## 1. VERSION — PASS

```
$ cd /home/mmonk/Downloads/LSTM_VS_LLM/mmemz && python3 -c "import mmemz; print(mmemz.__version__)"
0.1.0
rc=0
```
```
$ grep -nE '^(name|version)|name *=|version *=' pyproject.toml
32:name = "mmemz"
33:version = "0.1.0"
38:authors = [{ name = "mmonk" }]
```

## 2. TESTS — PASS (suite), PASS (both mutations are caught)

```
$ python3 -m unittest discover -s tests
Ran 193 tests in 0.022s
OK
```
Test-method count, per file (`grep -c "    def test_" tests/test_*.py` → `tests/test_bm25.py:23`, `test_entities.py:69`, `test_graph.py:26`, `test_scoring.py:45`, `test_service.py:30`; total `193`). The "Ran 193" figure agrees with the grep count, so no test is being skipped or parameterised away (no `skip`/`expectedFailure` decorators exist).

**Mutation A — `max()` → `sum()` in the bounded entity-relevance arm** (`mmemz/scoring.py:230-233` → `best += boost`):

```
FAIL: test_two_entities_are_bounded_by_the_best_one (test_scoring.TestMaxNeverSum...)
  File ".../artifacts/tests/test_scoring.py", line 116, in test_two_entities_are_bounded_by_the_best_one
    self.assertLessEqual(result, 0.4 + 1e-12)
AssertionError: 0.75 not less than or equal to 0.400000000001

FAIL: test_mutation_a_sum_instead_of_max_fails_here (...)
  File ".../artifacts/tests/test_scoring.py", line 293
    self.assertAlmostEqual(result, 0.45, places=12)
AssertionError: 0.9 != 0.45 within 12 places (0.45 difference)

Ran 193 tests in 0.023s
FAILED (failures=7)
```

**Mutation B — delete the 0.5 clamp in `bounded_multiplier`** (`mmemz/scoring.py:414-416`):

```
FAIL: test_multiplier_is_clamped_at_the_ceiling (test_scoring.TestZeroFallback...)
  File ".../artifacts/tests/test_scoring.py", line 230
    self.assertEqual(bounded_multiplier(0.9), 1.5)
AssertionError: 1.9 != 1.5
FAILED (failures=2)
```

Both scoring invariants are load-bearing and named tests defend them.

## 3. DEPENDENCY HONESTY — PASS

AST walk over every `mmemz/*.py` (`ast.Import` + `ast.ImportFrom`), distinct top-level modules:

```
DISTINCT TOP-LEVEL IMPORTS: ['(relative)', '__future__', 'dataclasses', 'math', 're', 'typing', 'unicodedata']
STDLIB: ['__future__', 'dataclasses', 'math', 're', 'typing', 'unicodedata']
THIRD-PARTY / OFFENDERS: []
```
Cross-checked with `grep -nHE "^\s*(import|from)\s" mmemz/*.py` — same set, no offender. `python3 -S` (site-packages disabled) imports all seven modules: `imported with site disabled OK 0.1.0`, rc=0. `pyproject.toml:50-51` = `# Stdlib only. ... dependencies = []`. Only `setuptools>=64` is a **build** requirement (`pyproject.toml:28`), not a runtime one.

## 4. VENDOR FIDELITY — FAIL on both self-reports

### 4a. `graph_walk.py` — 20 differing lines, but NOT header-only

```
$ diff ../memory_service_v_2.3.0/agentmem/graph_walk.py mmemz/graph_walk.py
0a1,13      <- 13-line provenance header (new)
3d15        <- source line 3 DELETED
58a71,76    <- 6 lines added: blank + SOURCE_PATH/SOURCE_LINES comment+consts + blank
```
Counts: `<-lines=1 >+lines=19 total_differing=20`. So the *number* 20 is right, the *composition* is not.

Byte-level reconstruction of the body (vendored minus the 13-line header minus the 6 inserted lines):
```
vendored body line count: 460  source line count: 461
body == src (claim as written)?        False
body == src minus line 3?              True
deleted source line 3 was:             b'anchor: e1f02845'
that text present anywhere in vendored? False
```
**The header claim is FALSE as written.** `mmemz/graph_walk.py:9-11` says "Everything below line 1 of the source is byte-identical to it. The ONLY additions are this comment block and the SOURCE_PATH / SOURCE_LINES constants". A *source body line* (`anchor: e1f02845`, line 3) was silently dropped. The claim about the additions is accurate; the claim about byte-identity below line 1 is not. `Source lines: 460` in the header is correct (`wc -l` → 460).

### 4b. `vecops.py` — 92 differing lines, NOT the self-reported 87

```
$ diff ../memory_service_v_2.3.0/agentmem/vecops.py mmemz/vecops.py | ... 
<-lines=44 >+lines=48 total_differing=92
hunks: 1c1,26 5c30 7,10c32,36 12,13c38 20,25c45,54 35,42d63 47,52c68,69 57c74 59,61c76 65,76c80
```
No diff convention yields 87: plain/**-B**/`--strip-trailing-cr` → 92; `-b`/`-w` → 90; `diff | wc -l` → 112; `diff -u | wc -l` → 126; body-only (vendored minus its 24-line header) → 68. **`mmemz/vecops.py:20` ("reports 87 differing lines") is FALSE; the true figure is 92.**

Hunk-by-hunk, all 10 hunks accounted for:
1. `1c1,26` — 24-line provenance header replaces the 1-line source docstring opener.
2. `5c30`, `7,10c32,36`, `12,13c38` — docstring reworded (em-dash → `--`, "The fix is" → "The fix in the source is", numpy-is-present sentence replaced by "mmemz carries no numpy").
3. `20,25c45,54` — **the `try: import numpy as np` probe and `_HAS_NUMPY` constants removed**, replaced by `__all__` + `SOURCE_PATH` + `SOURCE_LINES = 75`.
4. `35,42d63` — the `if _HAS_NUMPY:` block inside `cosine` deleted.
5. `47,52c68,69` — `normalize` loses its numpy branch and its `| "np.ndarray"` return annotation → `-> list[float]`.
6. `57c74`, `59,61c76`, `65,76c80` — `cosine_batch`'s numpy BLAS branch deleted; docstring de-numpyfied; keeps the `[cosine(r, query) for r in rows]` fallback.

**The header's substantive claim IS TRUE of the code**: what remains is exactly the source's pure-Python fallback path. Verified behaviourally against an independent reference cosine — `([1,0],[1,0])→1.0`, `([0,1],[1,0])→0.0`, `([0,0],[1,1])→0.0`, `([1,2,3],[4,5,6])→0.9746318461970762`, `([1e-9,2],[3,4e9])→1.0` — all identical to the reference; `normalize([3,4])→[0.6,0.8]`, `normalize([0,0])→[0.0,0.0]`, `cosine_batch([],[1,2])→[]`, `cosine_batch([[0,0],[1,0]],[1,0])→[0.0,1.0]`. No formula, edge case or return type changed. `SOURCE_LINES = 75` matches `wc -l`. So: **number false, claim true.**

## 5. SIX PHASES — PASS on existence, PASS on supersede, one real defect

`def` lines in `mmemz/service.py` (banner comments at 361/442/492/548/605/716):

| Phase | method | def line |
|---|---|---|
| 1 recognize | `recognize` | **362** |
| 2 inject | `inject` | **549** |
| 3 extract | `extract` | **443** |
| 4 consolidate | `consolidate` | **493** |
| 5 return_entities | `return_entities` | **606** |
| 6 reconstruct | `reconstruct` | **717** |

All six are real code, not stubs — each is exercised by `tests/test_service.py:257` (`test_phase_methods_exist_under_their_names` and five behavioural tests). Cosmetic: the phase banners appear in source order 1,3,4,2,5,6 — phase 2 is defined *after* phase 4.

**Supersede genuinely supersedes (not duplicate):**
```
consolidate report: {'memory_id': 'm2', 'added': [['acme corporation','is','based in paris']],
                     'restated': [], 'superseded': [['acme corporation','is','based in london']],
                     'memory_count_delta': 0}
len before: 1 -> len after: 1
live props:       [('acme corporation','is','based in paris')]
superseded props: [('acme corporation','is','based in london',
                    {'superseded_by': 'based in paris', 'superseded_at': 'consolidate'})]
```
Old proposition retained with the `superseded_by` stamp, count flat. Correct.

**DEFECT — `memory_count_delta` is measured after the memory has already been created**, so it reports 0 for a creation it caused. `mmemz/service.py:520-525` inserts on a miss and only *then* takes `before = len(self.memories)`:
```
len before consolidate on a BRAND-NEW id: 0
len after : 1
report.memory_count_delta = 0   (a memory WAS created)
```
The docstring at `service.py:124-126` ("`memory_count_delta` is always `0` by construction") is true, but only because `before` is sampled at the wrong point — the metric cannot detect growth even in principle. `ConsolidationReport` defaults `memory_count_delta: int = 0` (`service.py:133`) and is never assigned a nonzero value in practice. Low severity (`consolidate` is called by `remember` after insertion), but the "measured, not asserted" claim at `service.py:507-508` is overstated.

## 6. DO-NOT-PORT COMPLIANCE — PASS (a)(b)(c), with a caveat on (b)'s wording

**(a) bounded multi-hop, not single-hop — PASS.** `mmemz/graph.py:242-270` `BipartiteGraph.expand` delegates to `graph_walk.expand_reachable` (import at `graph.py:39`); there is no second BFS. `reconstruct("m1")` returns **10 hops** at depths {1,2,3}, including a *sibling memory* reached at depth 2 through the shared entity `acme corporation` (`{'from': 'acme corporation', 'to': 'm2', 'depth': 2}`) — impossible with a single entity hop. `max_depth=None` still terminates (len 10, max depth 3) thanks to the visited-set + `max_nodes` budget.

**(b) 0.82, not 0.95 — PASS.** `MERGE_THRESHOLD: float = 0.82` (`entities.py:107`), runtime `EntityStore().threshold == 0.82`. `grep -rnE "[:=] *0\.95" mmemz/*.py` finds **no assignment** — only prose at `entities.py:26` quoting mem0's source. Caveat: the brief's premise ("mem0's looser 0.95") is the wrong word, and the package says so at `entities.py:30-31,648-671` ("0.95 is numerically the STRICTER number ... not because 0.95 is a lower bar"). The *choice* of 0.82 is right; only the label "looser" is unsupportable, and mmemz does not use it as if it were.

**(c) reconciliation, not ADD-only — PASS in `consolidate`** (same-triple → `restated`; same-head-different-object → supersede; new-head → `added`). Note that at the *service* level this is undermined — see 7b.

## 7. HONESTY — FAIL: eight untrue or unsupported claims

### 7a. README's score-table *explanation* is false (numbers are right) — most serious
`README.md:82-96` claims: "`m2` is linked to two of the query's three entities weakly; `m3` to one of them strongly. Legacy *sums* the links and puts `m2` first; bounded takes the *max*". Measured on the README's own query:
```
query entity scores: {'london': 0.5}      # ONE of the three query words resolved
m2: linked=['acme corporation','london']  MATCHED=['london']  n_matched=1
m3: linked=['acme corporation','ada lovelace','charles babbage'] MATCHED=[] n_matched=0
max n_matched over all six memories: 1
```
`m2` matches **one** query entity, not two; **no memory matches two**, so the max-vs-sum term **never fires** on this query. The inversion is caused entirely by the different per-link **weight** and **algebra**:
```
bounded: relevance 0.25 -> multiplier 1.25      (0.5*0.5, multiplicative: sim*1.25)
legacy : relevance 0.50 -> multiplier 0.6925    (0.1925 + 0.50, ADDITIVE)
```
`score_candidate` confirms the two modes are not the same arithmetic: `legacy: 1.1925`, `bounded: 0.28875` for identical inputs. The published numbers reproduce *exactly* (`bounded: m3 0.3849, m2 0.2406 mult 1.2500 matched=['london']`; `legacy: m2 0.6925 rel 0.5000 mult 0.6925`), so the table is honest — the causal story printed under it is not. The max-vs-sum distinction *is* real and tested (`test_scoring.py:288`, and `bounded_entity_relevance(['ada lovelace','charles babbage'])` = 0.2498 vs legacy = 1.0), it just is not what produces the README's table.

### 7b. README's consolidate claim is false of the run it describes
`README.md:98-100`: "`m5` restates `m1` word for word. `remember()` reconciles rather than appending, so `memory_count_delta == 0` and `len(service.memories)` does not move."
```
len(memories) after ingesting all 6 corpus rows: 6
ids: ['m1','m2','m3','m4','m5','m6']
m1 and m5 both present: True
m1.text == m5.text (verbatim restatement): True
m1 live propositions: []      m5 live propositions: []
```
The count never moves because `m5` is a *different id* — nothing was reconciled. Worse, reconciliation could not have fired: `extract("Acme Corp hired Ada Lovelace as a compiler engineer.")` returns `[]` (the splitter's verb list has no "hired"), so both `m1` and `m5` store zero propositions. The verbatim restatement **did accumulate as a second memory** — precisely the ADD-only defect the package claims to close. Per-call trace: `remember(m1)` len 0→1 `delta=0 added=0`; `remember(m5)` len 4→5 `delta=0 added=0`.

### 7c. README design rule 2 ("cost") is false — the bound is on the value, not the cost
`README.md:59-62`: "Cost must never scale with how many links a memory has accumulated — a hub node with a thousand edges must not make recall of that memory more expensive than one with three."
`mmemz/scoring.py:221` is `for eid in linked_entities:` — O(links) per candidate, plus an O(links) `linked_memory_counts` pass at `service.py:596`. Measured per call:
```
n_links=    10  per-call=      2.0 us
n_links=  1000  per-call=    178.9 us
n_links= 10000  per-call=   1802.0 us
```
Linear in link count — a 10 000-edge hub is ~900x more expensive than a 10-edge memory. The *value* is correctly bounded at 0.5 (that part is true and mutation-tested). The rule as written is about cost and is not true of the code.

### 7d. README module map — `bm25` is not wired in
`README.md:32` says `service.py` does "the wiring that binds graph + scoring + bm25 together". `grep -n "bm25" mmemz/service.py` → rc=1, **no matches**. The only mentions of `bm25` outside `bm25.py` are two docstring lines in `mmemz/__init__.py:9,17`. `bm25.py` is a latent module (README does mark it "latent" in the status column at line 30, which softens this) but the "wiring" sentence at line 32 is untrue.

### 7e. README's description of `scoring.py` is untrue on three counts
`README.md:29`: "mem0 v2.2.1's scoring maths — similarity, recency/importance weighting, and the entity boost — reimplemented against `math` and `json` only."
- **similarity**: no such function — `grep -n "def .*sim\|def similarity" mmemz/scoring.py` → rc=1. Similarity lives in `service.py:692` `_similarity`.
- **recency/importance weighting**: absent — `grep -ciE "def .*(recency|importance|decay|time)" mmemz/scoring.py` → `0`.
- **`math` and `json`**: `grep -c json` → `0`; `grep -c math` → 7, all in prose (`.. math::`, the word "maths"). The real imports are `__future__` and `typing` only (`scoring.py:51,53`). The pyproject rationale block repeats the same error at `pyproject.toml:18-20` ("uses only `math` and `json`").

### 7f. README's description of `graph.py` is untrue
`README.md:31`: "nodes, **typed edges**, **per-memory link caps**, and the **reconcile-by-supersede update path**". `grep -niE "cap|limit|supersed|reconcile|typed|edge_type" mmemz/graph.py` → rc=1, **no matches**. `graph.py` is two `dict[str, set[str]]` maps plus a subtraction; there are no typed edges, no link cap, and no supersede path (supersede lives in `service.consolidate`). `neighbors` even documents "every edge is 1.0 because this store holds no edge strengths" (`graph.py:218-221`).

### 7g. README's `vecops` "small top-k" does not exist
`README.md:34` lists "cosine, normalisation, small top-k". `grep -n "top" mmemz/vecops.py` → rc=1; same in the source. `__all__` is `['cosine','cosine_batch','normalize','SOURCE_PATH','SOURCE_LINES']`.

### 7h. `mmemz/__init__.py` module docstring is stale and self-contradicting
`__init__.py:3-12`: "**mmemz is a from-scratch rebuild**" (README:13 says "not a fork" but also says it vendors two modules — minor), "This is version 0.1.0 and it is honestly **a skeleton**: the package currently exposes its version string ... and nothing else. The modules named in `README.md` — `entities`, `scoring`, `bm25`, `graph`, `service`, plus the vendored `graph_walk` and `vecops` — are **planned and not yet written**", and `:17-19` "Deliberately empty: submodules ... **do not exist yet**". All seven exist, import cleanly, and pass 193 tests. A docstring asserting the code is unwritten, shipping next to the code.

### 7i. Claims verified TRUE (for balance)
- `README:16-17,79-80` "193-test ... `Ran 193 tests ... OK`" — true (see §2).
- `README:87-91` every score and multiplier in the table — reproduces digit-for-digit.
- `README:102-103` `len(path) == 10` — true (`reconstruct(m1) len(path) = 10`).
- `README:99` `memory_count_delta == 0` — true (see §7b for why that is not a compliment).
- `README:116-118` alias dedupe — true: `len(store) = 5` vs 13 raw proper-noun runs; and `normalize(_expand_legal_forms('Acme Corp')) == normalize(_expand_legal_forms('ACME Corporation')) == 'acme corporation'`, so the stated mechanism holds.
- `scoring.py:47` "Stdlib only: `__future__` and `typing`" — true.
- `graph_walk.py:4` `Source lines: 460`, `vecops.py:4` `Source lines: 75` — both match `wc -l`.
- `scoring.py` provenance anchors (`main.py:1822`, `:1807`, `:1815`) are consistent across the module, the docstrings and the tests.

---

## Summary of what needs fixing (nothing was changed)

| # | Severity | Item |
|---|---|---|
| 1 | HIGH | `README.md:82-96` — wrong causal explanation of the mode inversion; sum-vs-max never fires on that query (max n_matched = 1). Weight + algebra differences are the real cause. |
| 2 | HIGH | `README.md:98-100` — the m5/m1 "restatement did not grow the count" story is false; both memories exist, both store zero propositions, and `extract()` cannot parse "hired". |
| 3 | HIGH | `mmemz/graph_walk.py:9-11` — "byte-identical below line 1" is false; source line 3 (`anchor: e1f02845`) was dropped. |
| 4 | MED | `mmemz/vecops.py:20` — self-reported "87 differing lines" is actually 92 (the substantive claim holds). |
| 5 | MED | `README.md:59-62` — design rule 2's cost bound is O(links) in practice (2.0us → 178.9us → 1802.0us for 10/1000/10000 links). |
| 6 | MED | `mmemz/__init__.py:3-19` — docstring says the modules are "planned and not yet written"; all seven exist and pass. |
| 7 | MED | `README.md:29,31,32,34` + `pyproject.toml:18-20` — module-map claims (recency/importance, `math`+`json`, typed edges, link caps, supersede in graph.py, bm25 wiring, top-k) not true of the code. |
| 8 | LOW | `mmemz/service.py:520-525` — `memory_count_delta` sampled after insertion, so it cannot ever report the growth it causes. |
| 9 | COSMETIC | `mmemz/service.py` phase banners out of order (1,3,4,2,5,6). |

VERDICT: 5 of 7 items PASS (version, tests incl. two caught mutations, dependency honesty, six phases + genuine supersede, do-not-port a/b/c); item 4 FAILS on both header self-reports (graph_walk's byte-identity claim is false — a source line was dropped; vecops' 87 is really 92, though its substantive claim holds); item 7 FAILS with eight README/docstring claims that are not true of the code, two of them HIGH (the mode-inversion explanation and the restatement/supersede story).
