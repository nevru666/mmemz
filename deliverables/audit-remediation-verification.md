<!-- anchor: 6a613970 -->
# Remediation verification — nine audit findings, commit `7e8abbd`

Read-only re-verification of `deliverables/audit-mmemz-5781a81b.md` §"Summary of what needs fixing".
Package: `/home/mmonk/Downloads/LSTM_VS_LLM/mmemz` at HEAD `7e8abbd` (`git log --oneline -1` → `7e8abbd Close all nine independent-audit findings; 193 -> 194 tests`). No tracked file was edited (`git status --porcelain` → empty).
`<artifacts>` = `.shrimp/contexts/goal-extract-mem0-v2-2-1-s-maths-and-cod-ad1580e3/6a613970/artifacts`.

## 1. HIGH — README causal explanation of the mode inversion
**PASS.** `python3 <artifacts>/probe1.py` (rc=0) builds a fresh `MemoryService` from
`from test_service import CORPUS`, `remember()`s the six memories, then calls `search(q, 6, mode)`
for both modes, **without** calling `recognize()` first. Verbatim:

```
== mode=bounded
   m3 score=0.3849 sim=0.3849 rel=0.0000 mult=1.0000 n_matched=0 matched=()
   m2 score=0.2406 sim=0.1925 rel=0.2500 mult=1.2500 n_matched=1 matched=('london',)
   m1 score=0.1925 sim=0.1925 rel=0.0000 mult=1.0000 n_matched=0 matched=()
   m5 score=0.1925 sim=0.1925 rel=0.0000 mult=1.0000 n_matched=0 matched=()
   m6 score=0.1925 sim=0.1925 rel=0.0000 mult=1.0000 n_matched=0 matched=()
   m4 score=0.0000 sim=0.0000 rel=0.0000 mult=1.0000 n_matched=0 matched=()
== mode=legacy
   m2 score=0.6925 sim=0.1925 rel=0.5000 mult=0.6925 n_matched=1 matched=('london',)
   m3 score=0.3849 sim=0.3849 rel=0.0000 mult=0.3849 n_matched=0 matched=()
   m1 score=0.1925 sim=0.1925 rel=0.0000 mult=0.1925 n_matched=0 matched=()
   m5 score=0.1925 sim=0.1925 rel=0.0000 mult=0.1925 n_matched=0 matched=()
   m6 score=0.1925 sim=0.1925 rel=0.0000 mult=0.1925 n_matched=0 matched=()
   m4 score=0.0000 sim=0.0000 rel=0.0000 mult=0.0000 n_matched=0 matched=()
== max n_matched bounded: 1
== len(memories): 6
```

`SearchResult` has no `n_matched` field, so `n_matched` = `len(r.matched_entities)` (`service.py:165`);
`len(memories)==6` shows no stray `recognize()` mutation. README `:100-110` states exactly this
(`max n_matched == 1`, "max-versus-sum term **never fires on this query**"), `:106-110` attributes the
inversion to "**the per-link weight**: the same link scores `0.5` legacy and `0.25` bounded" and
"**the algebra**: legacy *adds*… bounded *multiplies*", and `:112-118` points at the test that *does*
exercise max-vs-sum (`0.45` vs `1.8`). Every number reproduces.

## 2. HIGH — restatement story (m5 restates m1)
**PASS.** `python3 <artifacts>/probe2.py` (rc=0), lines A/B:

```
A) same text new id -> len(memories) = 2
B) same id supersede -> len(memories) = 1 live = [('ada lovelace', 'is', 'based in manchester')] superseded = [('ada lovelace', 'is', 'based in london')] delta = 0
```

The new-id limit is real: `len(memories) == 2` for identical text under `m5`. README `:120-128` now
shows the same-id transcript (`live` / `superseded` / `len(memories) == 1`) and `:137-142` states the
limit as measured. The false "m5 restated m1, count did not grow" proof is gone: `grep -n 'm5' README.md`
→ only `:138` (the limit) and `:147` (the historical `hired` note).

## 3. HIGH — `graph_walk.py` header differing-line count
**PASS.** `diff /home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0/agentmem/graph_walk.py mmemz/graph_walk.py | grep -c '^[<>]'` → `27` (rc 0; hunks `| grep -c '^[0-9]'` → `3`).
Header `graph_walk.py:9-11`: "Exactly three things differ, and `diff <source> mmemz/graph_walk.py`
(27 differing lines, 3 hunks)". **Measured 27 vs stated 27.** No byte-identity claim; the dropped source
line is named (`anchor: e1f02845`), confirmed by `diff … | grep -v '^[<>] #'` → `3d22` / `< anchor: e1f02845`
(the one body deletion).

## 4. MED — `vecops.py` header differing-line count
**PASS.** Same command against `vecops.py` → `92` (rc 0; hunks `10`). Header `vecops.py:20-21` claims
"reports 92 differing lines". **Measured 92 vs stated 92.**

## 5. MED — design rule 2, value vs cost
**PASS.** README `:61-68` rule 2 is now "**A per-memory bound on the entity arm's *value*.**" and says
"This is a bound on the score, **not on the cost**: the arm still iterates the memory's links (O(links)
per candidate…), measured at ~2 us for 10 links and ~1.8 ms for 10 000. … the value is bounded, the
traversal is not." No cost bound survives: `grep -n 'O(1)' README.md` → only `:31`, about
`entity_memory_count` (true of the reverse index, unrelated to the arm).

## 6. MED — `mmemz/__init__.py` docstring
**PASS.** `git diff b23857a 7e8abbd -- mmemz/__init__.py` shows "planned and not yet written" deleted and
replaced by a per-module list of what exists plus the named gaps ("``extract()`` is a small lexical
splitter, not an LLM call…; the entity arm bounds its *score* per memory but not the *work*…; and the BM25
module is unused"). `grep -n 'planned and not yet written' mmemz/__init__.py` → no match, rc 1.

## 7. MED — module map + `pyproject.toml`
**PASS on every sub-claim.**

| Claim (README) | Command | Output | Verdict |
| --- | --- | --- | --- |
| `:30` service does **not** call `bm25` | `grep -n 'bm25' mmemz/service.py` | no output, rc 1 | true |
| …runtime marker | `python3 -c "import mmemz.bm25 as b; print(b.is_used_by_service())"` | `False` | true |
| `:34` vecops has no top-k helper | `grep -n 'top' mmemz/vecops.py` | no output, rc 1 | true |
| `:31` graph.py unweighted edges, no link cap | `grep -n 'weight\|cap\|typed' mmemz/graph.py` | only `:213`/`:254`/`:354` docstring mentions of path weights; `:220-221` "here every edge is 1.0 because this store holds no edge strengths" | true |
| `:29` scoring.py does no recency/importance weighting | `grep -n 'recency\|importance' mmemz/scoring.py` | no output, rc 1 | true |
| `:29` scoring.py imports `__future__`+`typing`, not `math`+`json` | `grep -n '^import\|^from' mmemz/scoring.py` | `51:from __future__ import annotations` / `53:from typing import Any, Callable, Iterable, Mapping, Sequence` | true |
| …and uses no `math` | `grep -c 'math\.' mmemz/scoring.py` | `0` (rc 1) | consistent |
| pyproject | `grep -n 'dependencies' pyproject.toml` | `51:dependencies = []` | true |

Nit, not a finding: README `:17` and `:85` still say **193** tests; the suite runs 194.

## 8. LOW — `memory_count_delta` sampled before get-or-create
**PASS.** `mmemz/service.py:535` is `before = len(self.memories)`; the get-or-create is `:537-539`
(`mem = self.memories.get(memory_id)` … `self.memories[memory_id] = mem`). Sampling precedes insertion, with
an explicit comment saying why. Behavioural proof:

```
python3 -c 'from mmemz.service import MemoryService; from mmemz.entities import Proposition as P; s=MemoryService(); print(s.consolidate("never-seen",[P("a","is","b")]).memory_count_delta, len(s.memories))'
1 1
```

(rc 0.) `memory_count_delta == 1` for a get-or-create — impossible under the old ordering.

## 9. COSMETIC — phase banner order
**PASS.** `service.py:365-371` carries the required comment: "NOTE ON ORDER: the methods below are grouped
by *call site*, not by phase number, so the banners read 1, 3, 4, 2, 5, 6. That is deliberate -- ingest
(``remember``) runs 1->3->4 and query (``_rank``) runs 1->2->5…". Banner lines match the stated sequence:
`:375` (P1), `:458` (P3), `:508` (P4), `:569` (P2), `:627` (P5), `:735` (P6). The finding permitted the order
*only if* such a comment existed; it does.

## F1. Functional fix — `_PREDICATE_VERBS` parses "hired"
**PASS.** The callable is module-level `extract_propositions` (`entities.py:1050`); there is no
`mmemz.entities.extract` (`from mmemz.entities import extract` → `ImportError`, rc 1) — `extract` is the
*method* `service.py:455`, which delegates. Command:

```
python3 -c 'from mmemz.entities import extract_propositions as e; p=e("Acme Corp hired Ada Lovelace as a compiler engineer."); print(len(p), [(x.subject,x.predicate,x.object) for x in p])'
1 [('acme corp', 'hired', 'ada lovelace as a compiler engineer')]
```

(rc 0.) Exactly one proposition. `entities.py:957-961` adds the employment/commerce verbs with a comment
naming audit finding 2 — a code fix, not a doc edit.

## F2. Functional fix — restatement test asserts a non-empty fact list
**PASS — guard present and non-vacuous.** `tests/test_service.py:169-179`, verbatim:

```python
    def test_restating_a_fact_does_not_grow_the_memory_count(self):
        # GUARD FIRST. This test passed for a while on two EMPTY proposition lists
        # -- ``extract()`` could not parse "hired", so m1 and m5 both stored zero
        # facts and "restated or not added" was vacuously true. Assert there is a
        # real fact to restate, or the test proves nothing. Audit finding 2.
        live = self.svc.memories["m1"].propositions
        self.assertTrue(
            live,
            "m1 stored no propositions -- the restatement below would be vacuous",
        )
```

The guard line is `self.assertTrue(live, "m1 stored no propositions -- the restatement below would be
vacuous",)` at `:175-178`, **before** `before = len(...)` and before the `remember("m5", …)`. Non-vacuity
independently confirmed:

```
python3 -c "import sys; sys.path.insert(0,'tests'); from test_service import build_service; s=build_service(); print([(p.subject,p.predicate,p.object) for p in s.memories['m1'].propositions])"
[('acme corp', 'hired', 'ada lovelace as a compiler engineer')]
```

(rc 0.) `:190-194` then asserts `report.restated[0]` equals that exact triple.

## Suite run
```
python3 -m unittest discover -s tests
Ran 194 tests in 0.026s
OK
```

rc 0 (run as `python3 -m unittest discover -s tests > <artifacts>/ut.log 2>&1`; `grep -n '^Ran\|^OK\|^FAILED'
ut.log` → `3:Ran 194 tests in 0.026s` / `5:OK`). No skips, failures or errors. The commit's "193 -> 194
tests" matches.

## Incidental observations (not among the nine)
- README `:17` ("193-test stdlib suite") and `:85` ("`Ran 193 tests ... OK`") were not bumped to 194 —
  stale, and self-inconsistent with `CHANGELOG.md:54`.
- Four fixes (#3, #4, #5, #9) touch prose/comments only, but each was a documentation-accuracy finding and
  each now states a fact that reproduces (27, 92, the O(links) cost, the call-site ordering). None of them
  papers over wrong code.

## VERDICT
**9 of 9 closed.** Both functional fixes hold. No fix is cosmetic-only: the two findings with real
behavioural content (#2's unparseable `hired`, #8's delta ordering) have code changes at
`entities.py:957-961` and `service.py:535` behind tests, and every corrected documentation claim
reproduces verbatim under the command shown. Residual, outside the nine: README still says 193 tests
where the suite runs 194.
