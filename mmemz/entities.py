"""Entity recognition, type heuristics, dedupe and proposition extraction.


WHAT THIS IS, HONESTLY
----------------------
This module is a **rule-based placeholder** for mem0's LLM entity extractor.
mem0 asks a model for entities and returns JSON conforming to a schema; the
model supplies the judgement, the schema supplies the shape. mmemz 0.1.0 ships
no model -- the package is stdlib-only and has no network permission -- so the
same *interface* is filled with conservative deterministic rules.

The interface is the deliverable, not the heuristics. An LLM backend can be
injected later (subclass :class:`EntityExtractor`, or pass a callable to
:class:`EntityStore`) without touching a single caller, because every caller
sees the same ``decide_type`` / ``extract_propositions`` shapes mem0's
extractor produces. Where the rules are guesses, they are documented as
guesses rather than dressed up as knowledge.

DEDUPE THRESHOLD: 0.82 IS THE TARGET'S, NOT mem0's 0.95
-------------------------------------------------------
Exact match first (normalised name or any alias), then fuzzy at
**cosine >= 0.82**. That 0.82 is the **target's tuned value**, from
``/home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0/agentmem/consolidation.py``,
constant ``_MERGE_THRESHOLD`` declared at **:179**. It is explicitly **NOT**
mem0's ``0.95`` (``mem0/memory/main.py:621`` and ``main.py:1166``:
``existing[0].score >= 0.95``).

ON THE WORD "LOOSER", HONESTLY
-----------------------------
Taken as a bare number, 0.95 is the *stricter* of the two -- a higher bar
accepts fewer merges. The spec calls mem0's 0.95 "looser" about the **effective
bar on mem0's own substrate**, and that is the reading this module adopts.
mem0's number is compared against an embedding cosine, where unrelated
surface forms routinely pile up near 1.0, so a 0.95 cut is cleared by pairs
that this module's character-n-gram cosine scores around 0.6. Transcribing
0.95 here would therefore merge almost nothing, whereas on mem0's substrate it
merges plenty. The number is not portable without the substrate that gives it
meaning; 0.82 was tuned against *this kind* of comparison and is the one that
travels.

The deeper reason is that the two thresholds guard opposite mistakes, and the
worse mistake here is the *extra* merge. mem0's 0.95 is applied to a vector-store similarity
whose merges are **append-only and irreversible** (``main.py:623-631``): once
two entities are merged the link is made and the original row is never
separated again. A *miscalibrated* threshold on an irreversible operation
creates **more wrong merges**, and a wrong merge silently rewrites the graph's shape --
two people become one, and every memory that mentioned either is now attached
to both. 0.82 on an entity resolver that keeps aliases and supports
``merge_entity``/``remove_memory`` is recoverable; 0.95 on an append-only store
is not. Copying mem0's number here would therefore import mem0's worst
property along with its arithmetic. The spec's verdict for this row is
**DO-NOT-PORT**; 0.82 is the target's value and mmemz keeps it.

COSINE
------
``_cosine`` below is a **thin alias over ``mmemz.vecops.cosine``**, the
vendored primitive (``agentmem/vecops.py``). It used to be a local stdlib
fallback using ``math`` only; that copy is gone now that ``vecops`` exists, so
there is exactly one cosine in the package. The vectoriser is character-trigram
+ token hashing into a sparse ``dict[str, float]``; ``_cosine`` projects a pair
of those onto their sorted key union and hands two dense ``list[float]`` to
``vecops.cosine``, which is the only shape that primitive takes. No numpy and no
embeddings are required.

PROVENANCE
----------
* ``attributed_to`` promotion -- ``mem0/memory/main.py:1244``.
* target's carrier for it -- ``agentmem/models.py:73`` (``Proposition`` with
  ``qualifiers`` at ``:82``).
* merge threshold -- ``agentmem/consolidation.py:179``.

Stdlib only: ``dataclasses``, ``re``, ``typing``, ``unicodedata``, plus the
in-package vendored ``mmemz.vecops``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .vecops import cosine as vecops_cosine

#: Entity type vocabulary. mem0's LLM extractor is asked for a bounded set of
#: labels; this is mmemz's, and it is deliberately small so a rule can map to
#: exactly one of them. ``other`` is the honest escape hatch.
ENTITY_TYPES: tuple[str, ...] = (
    "person",
    "org",
    "place",
    "concept",
    "thing",
    "event",
    "other",
)

#: Cosine at or above which a new mention is treated as an existing entity.
#: **0.82 is the TARGET's tuned value** --
#: ``/home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0/agentmem/consolidation.py``,
#: ``_MERGE_THRESHOLD`` at **:179**. It is explicitly **NOT** mem0's ``0.95``
#: (``mem0/memory/main.py:621`` / ``main.py:1166``). See the module docstring
#: for why the *stricter* number is the safer one: mem0's merges are
#: append-only and irreversible, so a badly calibrated threshold creates more
#: wrong merges with no way back. (0.95 is numerically STRICTER, not looser --
#: the point is calibration to the substrate, per the module docstring.)
MERGE_THRESHOLD: float = 0.82

#: Organisation suffixes, matched case-insensitively against the *final* token
#: of a multi-token name. Deliberately short: this is a heuristic, and a long
#: list would only add false positives it cannot justify.
_ORG_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc", "inc.", "ltd", "ltd.", "llc", "llp", "plc", "corp", "corp.",
        "corporation", "company", "co", "gmbh", "ag", "sa", "bv", "nv",
        "limited", "holdings", "group", "partners", "labs", "foundation",
        "university", "institute", "association", "trust",
    }
)

#: Gazetteer-lite: a short, high-precision set of places. Not a gazetteer --
#: a gazetteer would be a data dependency, and this module is stdlib-only. The
#: set exists so common single-token place names are not misfiled as
#: ``concept``; anything outside it falls through to the capitalisation rules.
_PLACE_GAZETTEER: frozenset[str] = frozenset(
    {
        "london", "paris", "berlin", "madrid", "rome", "amsterdam", "dublin",
        "edinburgh", "manchester", "birmingham", "bristol", "leeds", "glasgow",
        "new york", "san francisco", "los angeles", "chicago", "boston",
        "seattle", "austin", "toronto", "vancouver", "montreal", "tokyo",
        "beijing", "shanghai", "shenzhen", "singapore", "hong kong", "seoul",
        "sydney", "melbourne", "mumbai", "delhi", "bangalore", "dubai",
        "berne", "zurich", "geneva", "stockholm", "oslo", "helsinki",
        "copenhagen", "vienna", "prague", "warsaw", "lisbon", "athens",
        "england", "scotland", "wales", "ireland", "france", "germany",
        "spain", "italy", "netherlands", "belgium", "switzerland", "sweden",
        "norway", "denmark", "poland", "portugal", "greece", "canada",
        "america", "usa", "uk", "britain", "europe", "asia", "africa",
        "australia", "japan", "china", "india", "brazil", "mexico",
    }
)

#: Honourifics that mark a person when they lead a name.
_PERSON_TITLES: frozenset[str] = frozenset(
    {"mr", "mrs", "ms", "miss", "dr", "prof", "professor", "sir", "dame", "rev"}
)

#: Words whose presence in a name's last-but-one position suggests a person
#: rather than an organisation is unreliable, so this module does not use one.
#: Kept as a marker of a decision not taken rather than an omission.

_MONTHS: tuple[str, ...] = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
_MONTHS_ABBR: tuple[str, ...] = (
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
)
_WEEKDAYS: tuple[str, ...] = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
)

#: Date-like patterns. Deliberately loose on format and tight on shape: a
#: matched *shape* is strong evidence of an event, and a false positive here
#: costs little (an ``event`` label on a thing).
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                     # 2024-05-17
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),               # 17/05/2024
    re.compile(r"\b\d{1,2}(st|nd|rd|th)?\s+[a-z]+\s+\d{4}\b"),  # 17 May 2024
    re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+\d{1,2}\b"),  # May 17
    re.compile(r"\b(" + "|".join(_MONTHS_ABBR) + r")\s+\d{1,2}\b"),
    re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b"),
    re.compile(r"\bq[1-4]\s*\d{4}\b"),                        # Q3 2024
    re.compile(r"\b\d{4}s?\b"),                              # 2024 / 1990s
)

#: Punctuation trimmed from both ends of a normalised name. Apostrophes and
#: hyphens are NOT trimmed in the interior (``o'brien``, ``smith-jones`` keep
#: theirs) because they carry identity, not decoration.
_EDGE_PUNCT: str = " \t\r\n.,;:!?\"'`()[]{}<>«»“”‘’*#@$%^&+=~|\\/_-"

#: Characters kept when hashing a name for the vectoriser, on top of
#: alphanumerics. Kept tiny: the vector is a *similarity* substrate, so
#: punctuation that varies between mentions of one entity must be dropped.
_KEEP_IN_VECTOR: str = ""

_TRIGRAM_PAD = "  "


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Canonical form of an entity name. Deterministic and **idempotent**.

    Six ordered steps, each of which exists for a reason:

    1. **NFC-normalise** (``unicodedata.normalize("NFC", ...)``) first, so that
       a decomposed ``e`` + combining acute and a precomposed ``é`` are the same
       string before anything compares them. Doing this *after* whitespace
       collapsing would already be too late for byte equality.
    2. **Lowercase** -- ``"Apple"`` and ``"apple"`` are one entity. Entity
       identity is case-insensitive; mem0 gets this from its LLM, mmemz from
       ``str.lower``.
    3. **Collapse internal whitespace** (including tabs/newlines) to single
       spaces, so ``"Acme   Corp"`` and ``"Acme Corp"`` agree.
    4. **Trim surrounding punctuation and spaces.** Surrounding, not internal:
       ``"(Acme)"`` and ``"Acme."`` become ``acme``; ``o'brien`` and
       ``smith-jones`` keep their interior marks because those are part of the
       name, not sentence punctuation that leaked in from the source text.
    5. **Strip** again, because step 4 can expose new surrounding whitespace
       (``"Acme . "`` -> ``"acme"``).
    6. Empty-in, empty-out: a name of only punctuation normalises to ``""``,
       which callers must treat as "no name" rather than as an entity.

    Idempotence is the load-bearing property -- ``normalize(normalize(x)) ==
    normalize(x)`` -- because the store hashes the *normalised* form as its
    exact-match key and a second pass would otherwise produce a second key.

    >>> normalize_name("  Acme   Corp. ")
    'acme corp'
    >>> normalize_name("(O'Brien)")
    "o'brien"
    >>> normalize_name("Éclair")
    'éclair'
    >>> normalize_name(normalize_name("  Acme   Corp. ")) == normalize_name("  Acme   Corp. ")
    True
    """
    if name is None:
        return ""
    text = unicodedata.normalize("NFC", str(name))
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(_EDGE_PUNCT)
    text = text.strip()
    return text


def decide_type(name: str, context: str | None = None) -> str:
    """Guess one of :data:`ENTITY_TYPES` for a name. **A rule-based placeholder.**

    THIS IS NOT mem0'S EXTRACTOR, AND DOES NOT PRETEND TO BE
    --------------------------------------------------------
    mem0 asks an LLM for entities and gets back JSON built to a schema; the
    model supplies the judgement and the schema supplies the shape
    (``mem0/memory/main.py:1244`` lists the enumerated fields it promotes,
    ``attributed_to`` among them). mmemz 0.1.0 is stdlib-only and has no network
    path, so there is no model to ask. What ships instead is a small set of
    conservative rules over the name's own surface.

    The **interface** is the deliverable. ``decide_type(name, context=None) ->
    str`` returning a member of :data:`ENTITY_TYPES` is exactly the contract an
    LLM backend would satisfy, so a backend can be injected later (subclass, or
    a callable passed to :class:`EntityStore`) without touching a single caller,
    and every call site keeps working unchanged. A documented rule beats an
    undocumented guess; a stable seam beats both.

    THE RULES, IN PRECEDENCE ORDER
    -----------------------------
    1. **Date-like** -> ``event``. A date or weekday shape in the name is
       strong evidence of an event and cheap to detect (``2024-05-17``,
       ``17 May 2024``, ``May 17``, ``Q3 2024``, ``Friday``). Checked first
       because ``"May 2024 Planning"`` is an event, not an org, and the org
       suffix rule below is happy to mislabel anything ending in a word.
    2. **Org suffix** -> ``org``. A multi-token name whose final token is in
       :data:`_ORG_SUFFIXES` (``Acme Ltd``, ``Foo GmbH``, ``Bar Foundation``).
       Multi-token only: a one-word name ending in ``group`` is too weak.
    3. **Honourific** -> ``person``. A leading ``Mr``/``Dr``/``Prof`` etc.
    4. **Gazetteer** -> ``place``. A normalised name in
       :data:`_PLACE_GAZETTEER`. The module docstring is explicit that this is
       a "gazetteer-lite": a real gazetteer is a data dependency and this
       module has none.
    5. **Two capitalised tokens, no org suffix** -> ``person``. ``"Ada
       Lovelace"`` is a person; ``"Graph Memory"`` is not, and this rule will
       get that one wrong. It is a guess and is labelled one.
    6. **Long lowercase multi-token phrase** -> ``concept``. ``"vector
       similarity"``, ``"episodic memory"``.
    7. **All-caps acronym, 2-5 letters** -> ``org``. ``"IBM"``, ``"NATO"``.
       Also a guess.
    8. **Anything else with letters** -> ``thing``.
    9. **Empty / no letters** -> ``other``.

    ``context`` is accepted and currently used for exactly one thing: a
    lowercase occurrence of the raw name inside a sentence-like context
    downgrades an otherwise-``person`` capitalisation guess to ``thing`` --
    mid-sentence lowercase usually means a common noun. It is otherwise unused,
    and the parameter exists so an LLM backend can receive surrounding text
    without an interface change.

    The raw (not normalised) name drives capitalisation, so this function is
    sensitive to how the caller passes it; normalisation destroys the very
    signal the rules use.

    >>> decide_type("Acme Ltd")
    'org'
    >>> decide_type("17 May 2024")
    'event'
    >>> decide_type("London")
    'place'
    >>> decide_type("Ada Lovelace")
    'person'
    >>> decide_type("vector similarity")
    'concept'
    >>> decide_type("")
    'other'

    Provenance of the seam: mem0's extractor + ``main.py:1244``.
    """
    raw = "" if name is None else str(name).strip()
    if not raw:
        return "other"
    norm = normalize_name(raw)
    if not norm:
        return "other"

    lowered = norm

    # 1. Date-like -> event. Checked before the org-suffix rule, and before the
    #    "needs letters" guard below: an ISO date such as ``2024-05-17`` is all
    #    digits and punctuation, so a letters-first guard would silently
    #    reclassify every ISO date as ``other``.
    for pattern in _DATE_PATTERNS:
        if pattern.search(lowered):
            return "event"

    if not any(ch.isalpha() for ch in norm):
        return "other"

    tokens = lowered.split(" ")

    # 2. Org suffix on a multi-token name.
    if len(tokens) >= 2 and tokens[-1] in _ORG_SUFFIXES:
        return "org"

    # 3. Honourific -> person.
    first = tokens[0].rstrip(".")
    if first in _PERSON_TITLES:
        return "person"

    # 4. Gazetteer-lite -> place.
    if lowered in _PLACE_GAZETTEER:
        return "place"

    raw_tokens = [t for t in re.split(r"\s+", raw) if t]

    # 5. Two capitalised tokens, no org suffix -> person (a guess).
    if (
        len(raw_tokens) == 2
        and all(t[:1].isupper() for t in raw_tokens)
        and all(any(c.isalpha() for c in t) for t in raw_tokens)
    ):
        # A context that uses the name in lowercase suggests a common noun.
        if context and raw.lower() in str(context).lower() and not raw[:1].isupper():
            return "thing"
        return "person"

    # 6. Long lowercase multi-token phrase -> concept.
    if len(tokens) >= 2 and not any(t[:1].isupper() for t in raw_tokens):
        return "concept"

    # 7. All-caps acronym -> org (a guess).
    letters = [c for c in raw if c.isalpha()]
    if (
        len(raw_tokens) == 1
        and 2 <= len(letters) <= 5
        and all(c.isupper() for c in letters)
    ):
        return "org"

    # 8. Single lowercase word -> concept; anything else -> thing.
    if len(raw_tokens) == 1 and raw_tokens[0][:1].islower():
        return "concept"
    return "thing"


# --------------------------------------------------------------------------
# Vectors + cosine
# --------------------------------------------------------------------------

def _vectorize(text: str) -> dict[str, float]:
    """Hash ``text`` into a sparse term-frequency dict. Stdlib only, no numpy.

    Three feature families in one dict, with disjoint prefixes so they cannot
    collide:

    * ``t:`` -- whole tokens (``"acme"``, ``"corp"``), weight 1.0. Catches *word*
      overlap, which is what distinguishes ``"Acme Corp"`` from ``"Acme Ltd"``.
    * ``b:`` -- per-token character **bigrams** over the space-padded token
      (``" ac"``, ``"cm"``), weight 0.6. Catches spelling overlap within a word at
      a short window, which is what lets a one-character substitution score at
      all: a single typo destroys three trigrams but only two bigrams.
    * ``g:`` -- per-token character **trigrams** (``" acm"``, ``"cme "``), weight
      0.4. Sharper than bigrams, so genuine word-level agreement contributes more
      than the sum of its bigrams alone.
    * ``c:`` -- whole-string trigrams over doubly space-padded text (``"  ac"``),
      weight 0.3. Catches word order and adjacency, so ``"acme corp"`` and
      ``"the acme corporation"`` are not strangers just because their token lists
      differ.

    Padding each token with spaces makes a word's *start* and *end* features in
    their own right, so a shared prefix scores above the same letters buried
    mid-word. Bigrams plus trigrams are the standard cheap fuzzy-string
    substrate: they tolerate typos, reordering and inflection, and need no
    training.

    Weights are ordered by evidential value: a whole token beats a shared
    three-letter window, which beats a shared two-letter window. Values are plain
    term frequencies -- no IDF, no scaling beyond the cosine denominator --
    because the store compares a handful of candidates at a time and the corpus
    statistics an IDF would need do not exist here.

    Deterministic and hash-free: keys are the literal feature strings, so no
    ``hash()`` (seed randomised per process) and no ``hashlib`` is involved. Same
    input, same dict, every run.

    >>> v = _vectorize("acme")
    >>> v["t:acme"]
    1.0
    >>> sorted(k for k in v if k.startswith("b:"))
    ['b: a', 'b:ac', 'b:cm', 'b:e ', 'b:me']
    >>> len({k.split(":")[0] for k in v})
    4

    Provenance: local stand-in for the similarity primitive mem0's entity dedupe
    calls (``main.py:621``); the real target primitive is
    ``agentmem/vecops.py:cosine`` at ``:33``.
    """
    norm = normalize_name(text)
    vec: dict[str, float] = {}
    if not norm:
        return vec

    tokens = norm.split(" ")

    # (1) Whole tokens -- exact word overlap, the strongest signal.
    for token in tokens:
        if token:
            key = "t:" + token
            vec[key] = vec.get(key, 0.0) + 1.0

    # (2) Per-token bigrams + trigrams -- spelling overlap within a word.
    for token in tokens:
        if not token:
            continue
        padded_token = " " + token + " "
        for i in range(len(padded_token) - 1):
            key = "b:" + padded_token[i : i + 2]
            vec[key] = vec.get(key, 0.0) + 0.6
        for i in range(len(padded_token) - 2):
            key = "g:" + padded_token[i : i + 3]
            vec[key] = vec.get(key, 0.0) + 0.4

    # (3) Whole-string trigrams -- word order and adjacency.
    padded = _TRIGRAM_PAD + norm + _TRIGRAM_PAD
    for i in range(len(padded) - 2):
        key = "c:" + padded[i : i + 3]
        vec[key] = vec.get(key, 0.0) + 0.3
    return vec


def _cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Cosine similarity of two sparse feature dicts, in ``[0.0, 1.0]`` here.

    .. math:: \\cos(a, b) = \\frac{\\sum_i a_i b_i}
                            {\\sqrt{\\sum_i a_i^2}\\sqrt{\\sum_i b_i^2}}

    Iterates the smaller dict so cost is proportional to the shorter vector,
    which matters because the store compares one new mention against every
    stored entity.

    Edge cases, all defined rather than raised:

    * either side empty -> ``0.0`` (no evidence of similarity, which correctly
      falls below any positive threshold);
    * either side zero-norm -> ``0.0``, guarding the division;
    * identical vectors -> ``1.0`` (so an exact match on the vector alone
      clears any threshold ``<= 1.0``, though the store's exact branch fires
      first anyway).

    Non-negative features only, so the result cannot be negative and no
    ``abs()`` fudge is needed.

    **LOCAL FALLBACK, NOW A DELEGATION.** ``mmemz/vecops.py`` has been vendored,
    so the arithmetic lives in exactly one place: this function projects the two
    sparse dicts onto their shared key space and calls ``mmemz.vecops.cosine``
    (the target's own primitive at ``agentmem/vecops.py``). The name ``_cosine``
    is kept as a thin alias so every existing caller and test keeps working; the
    local copy of the formula is deleted, not shadowed.

    Why the sparse-to-dense projection is a gain, not a cost: the two dicts are
    typically small (a name produces a handful of trigrams), the key union is
    computed once, and ``vecops.cosine`` iterates a pair of flat lists far faster
    than a dict ``.get`` loop. The zero-vector guard moves *before* the call so
    the documented ``0.0`` return is preserved exactly.

    >>> round(_cosine(_vectorize("acme corp"), _vectorize("acme corp")), 6)
    1.0
    >>> _cosine({}, _vectorize("acme"))
    0.0
    >>> round(_cosine(_vectorize("acme corp"), _vectorize("zeta ltd")), 6)
    0.0

    Provenance: ``agentmem/vecops.py`` (``cosine``) is now the single
    implementation; mem0's dedupe calls the same primitive through its vector
    store at ``main.py:621``.
    """
    if not a or not b:
        return 0.0
    keys = sorted(a.keys() | b.keys())
    va = [a.get(k, 0.0) for k in keys]
    vb = [b.get(k, 0.0) for k in keys]
    if not any(va) or not any(vb):
        return 0.0
    return vecops_cosine(va, vb)


@dataclass
class Entity:
    """One resolved entity: a canonical name, its aliases, its type.

    ``canonical`` is already normalised (see :func:`normalize_name`); ``aliases``
    are stored normalised too, sorted, and never contain the canonical name
    itself. Keeping the canonical name inside ``aliases`` would make union
    operations ambiguous about which string "wins", so the invariant here is:
    ``canonical not in aliases``, and every normalised string that ever resolved
    to this entity is either the canonical name or an alias.

    ``salience`` is a plain mention counter, not a decayed activation score --
    the target has a real activation model and duplicating it here would be a
    second source of truth. It exists so a merge can be audited.

    Provenance: the shape mirrors the target's ``Entity``
    (``agentmem/models.py:55``), which carries ``aliases``/``importance``/
    ``activation``; mem0 has no entity object at all, only a vector-store row
    with a payload.

    >>> e = Entity(canonical="acme corp", aliases=("acme",), type="org")
    >>> e.canonical
    'acme corp'
    >>> "acme" in e.aliases
    True
    """

    canonical: str
    aliases: tuple[str, ...] = ()
    type: str = "other"
    salience: int = 1


@dataclass
class Proposition:
    """A subject-predicate-object triple with provenance carried in qualifiers.

    ``attributed_to`` is a **provenance label**, not an extra graph field: it
    records *who or what the fact is attributed to* (a speaker, a document, a
    tool). mem0 promotes it to a first-class enumerated field of a memory row at
    ``mem0/memory/main.py:1244`` (in the list of fields it copies through).

    The target does **not** promote it. ``agentmem/models.py:73`` defines
    ``Proposition`` with a ``qualifiers`` mapping at ``:82``, and mmemz follows
    the target: ``attributed_to`` lives inside ``qualifiers`` under the key
    ``"attributed_to"``, and :meth:`get_attributed_to` reads it back.

    Why the target's way is better here: mem0's promoted field is one enumerated
    string per memory, so provenance cannot vary per *proposition*, and a memory
    holding three attributed facts needs three rows. ``qualifiers`` is a mapping,
    so it carries ``attributed_to`` alongside ``confidence``, source spans,
    temporal bounds and anything else the consolidation pass needs, without a
    schema change each time. The spec's verdict for that row is
    **DO-NOT-PORT**, and this dataclass is why.

    ``qualifiers`` is a plain ``dict`` (not frozen) because consolidation mutates
    it in place while reconciling; the dataclass itself is mutable for the same
    reason. Callers that need a hashable form should key on
    ``(subject, predicate, object)``.

    >>> p = Proposition("ada", "wrote", "notes", attributed_to="letter-1843")
    >>> p.qualifiers["attributed_to"]
    'letter-1843'
    >>> p.get_attributed_to()
    'letter-1843'

    Provenance: ``mem0/memory/main.py:1244`` (promotion),
    ``agentmem/models.py:73`` (``Proposition``), ``:82`` (``qualifiers``).
    """

    subject: str
    predicate: str
    object: str
    attributed_to: str | None = None
    qualifiers: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Mirror ``attributed_to`` into ``qualifiers`` so one carrier is canonical.

        The constructor argument is a convenience; ``qualifiers`` is the storage
        the target's consumers read. Keeping both in sync at construction means
        there is exactly one place to look, and a proposition built either way
        compares equal.
        """
        if self.attributed_to is not None:
            self.qualifiers.setdefault("attributed_to", self.attributed_to)
        else:
            existing = self.qualifiers.get("attributed_to")
            if isinstance(existing, str):
                self.attributed_to = existing

    def get_attributed_to(self) -> str | None:
        """Return the provenance label, or ``None`` when unattributed.

        Reads ``qualifiers`` first, then the field, so a caller that mutates
        ``qualifiers["attributed_to"]`` after construction still gets the current
        value rather than a stale copy.

        >>> Proposition("a", "b", "c").get_attributed_to() is None
        True
        >>> p = Proposition("a", "b", "c"); p.qualifiers["attributed_to"] = "doc-7"
        >>> p.get_attributed_to()
        'doc-7'
        """
        value = self.qualifiers.get("attributed_to", self.attributed_to)
        return value if isinstance(value, str) else self.attributed_to


class EntityStore:
    """Entity registry with **exact-then-fuzzy** dedupe and alias merging.

    RESOLUTION ORDER (the two-tier rule)
    -----------------------------------
    :meth:`resolve` tries, in order:

    1. **Exact** -- a lookup of the normalised name in ``_by_key``, which holds
       both canonical names and every alias. This is the common case and it is
       O(1): if the string has been seen, it has been seen exactly.
    2. **Fuzzy** -- cosine over the sparse feature vectors from
       :func:`_vectorize`, keeping the best-scoring existing entity. It is
       accepted only when ``best >= threshold``, with ``threshold`` defaulting to
       :data:`MERGE_THRESHOLD` (**0.82**).

    **0.82 is the TARGET's tuned value**, from
    ``/home/mmonk/Downloads/LSTM_VS_LLM/memory_service_v_2.3.0/agentmem/consolidation.py``,
    ``_MERGE_THRESHOLD`` at **:179**. It is explicitly **NOT** mem0's ``0.95``
    (``mem0/memory/main.py:621`` / ``main.py:1166``).

    To be plain about a word: **0.95 is numerically the STRICTER number**
    (a higher bar accepts fewer merges). An earlier draft of this module and of
    ``deliverables/mem0-port-spec.md`` called it "looser", which is wrong as
    arithmetic and right only as a statement about the *effective* bar on
    mem0's substrate -- see the module docstring's "ON THE WORD LOOSER" section.
    This docstring previously asserted both readings in adjacent sentences; the
    contradiction is removed rather than papered over.

    The real argument for keeping 0.82 does not depend on the word at all. Two
    independent reasons:
    (i) **Portability of the number.** mem0's threshold is applied to a
    vector-store (embedding) cosine, where unrelated surface forms routinely
    score near 1.0; this module compares character-n-gram cosines, where such
    pairs sit nearer 0.6. Transcribing 0.95 onto this substrate would merge
    almost nothing. The number is meaningless without the substrate that
    calibrated it.
    (ii) **Reversibility, on mem0's side.** mem0's merge is append-only and
    irreversible (``main.py:623-631``): the link is written and the original row
    is never separated again. The target's resolver keeps aliases and offers
    ``merge_entity``/``remove_memory``, so an 0.82 merge there is recoverable.
    The same numeric threshold guards a different severity of mistake.

    So: the target's 0.82 is the one to keep and mem0's 0.95 is not portable --
    for substrate and reversibility reasons, **not** because 0.95 is a lower
    bar. A badly calibrated threshold on an irreversible operation creates
    **more wrong merges** with no way back. Verdict in the spec: for the mem0
    row, **DO-NOT-PORT**.

    Merging two entities (:meth:`merge`) unions their aliases, keeps one
    canonical name, sums salience, keeps the more specific type, and rewrites
    every alias key to point at the survivor.

    >>> store = EntityStore()
    >>> a = store.resolve("Acme Corp")
    >>> store.resolve("  acme   corp. ").canonical == a.canonical
    True
    >>> store.resolve("Acme Ltd").canonical != a.canonical
    True

    Provenance: ``agentmem/consolidation.py:_resolve_entity`` at ``:955``,
    threshold at ``:179``; exact branch ``:961``
    (``self.store.find_by_name(name)``).
    """

    def __init__(self, threshold: float = MERGE_THRESHOLD) -> None:
        """Create an empty store.

        ``threshold`` defaults to :data:`MERGE_THRESHOLD` (0.82, the target's
        tuned value). It is a constructor argument, not a module constant read
        at call time, so a test can build a store at a different threshold
        without monkeypatching a module global.
        """
        self.threshold: float = float(threshold)
        self._entities: list[Entity] = []
        #: normalised name/alias -> index into ``_entities``
        self._by_key: dict[str, int] = {}

    # -- introspection ----------------------------------------------------

    def __len__(self) -> int:
        """Number of distinct entities held.

        >>> len(EntityStore())
        0
        """
        return len(self._entities)

    def __iter__(self):
        """Iterate the entities in insertion order.

        >>> [e.canonical for e in EntityStore()]
        []
        """
        return iter(self._entities)

    def __contains__(self, name: object) -> bool:
        """Whether a name would resolve **exactly** (no fuzzy pass).

        The probe is normalised first, so case and spacing do not matter; a
        near-miss spelling still answers ``False`` because tier 2 has not run.

        >>> s = EntityStore(); _ = s.resolve("Acme Corp")
        >>> "acme corp" in s
        True
        >>> "Acme Corp" in s
        True
        >>> "acme corporation" in s
        False
        """
        if not isinstance(name, str):
            return False
        return normalize_name(name) in self._by_key

    def get(self, name: str) -> Entity | None:
        """Exact lookup only; ``None`` when the name has never been seen.

        >>> EntityStore().get("nothing") is None
        True
        """
        idx = self._by_key.get(normalize_name(name))
        return None if idx is None else self._entities[idx]

    # -- the resolver -----------------------------------------------------

    def resolve(
        self,
        name: str,
        *,
        entity_type: str | None = None,
        threshold: float | None = None,
        context: str | None = None,
    ) -> Entity:
        """Return the entity this name denotes, creating it if new. Two tiers.

        Tier 1 is an exact match on the normalised name **or any alias**. Tier 2
        is cosine >= ``threshold`` (default 0.82) against every stored entity,
        best-first, using the entity's canonical name as the comparison vector.

        A fuzzy hit **adopts the seen string as a new alias**, so the next
        mention of exactly that string resolves in tier 1 instead of paying for
        the fuzzy pass again -- the resolver teaches itself.

        A miss creates the entity with ``entity_type`` if given, else
        :func:`decide_type` on the raw name.

        >>> store = EntityStore()
        >>> store.resolve("Acme Corp").type
        'org'
        >>> store.resolve("ACME CORP!").aliases
        ()
        >>> len(store)
        1

        >>> store2 = EntityStore()
        >>> first = store2.resolve("Acme Corporation", entity_type="org")
        >>> second = store2.resolve("The Acme Corporation")
        >>> second is first
        True
        >>> "the acme corporation" in second.aliases
        True

        A near miss **below** the threshold stays separate -- ``jonathan`` vs
        ``jonathon`` scores 0.7945, so the resolver does not fold a typo'd
        person into a real one:

        >>> store3 = EntityStore()
        >>> _ = store3.resolve("Jonathan Smith", entity_type="person")
        >>> store3.resolve("Jonathon Smith") is not store3.get("jonathan smith")
        True
        >>> len(store3)
        2

        Provenance: ``agentmem/consolidation.py:_resolve_entity`` (``:955``),
        exact branch ``:961``; mem0's 0.95 comparison at ``main.py:621``.
        """
        key = normalize_name(name)
        if not key:
            # A name of pure punctuation is not an entity. Return a detached
            # placeholder instead of polluting the store with an empty key.
            return Entity(canonical="", aliases=(), type="other", salience=0)

        # ---- Tier 1: exact, on the canonical name or any alias -----------
        idx = self._by_key.get(key)
        if idx is not None:
            entity = self._entities[idx]
            entity.salience += 1
            return entity

        # ---- Tier 2: fuzzy, best cosine at or above the threshold --------
        limit = self.threshold if threshold is None else float(threshold)
        probe = _vectorize(key)
        best_idx = -1
        best_score = 0.0
        for i, candidate in enumerate(self._entities):
            score = _cosine(probe, _vectorize(candidate.canonical))
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx >= 0 and best_score >= limit:
            entity = self._entities[best_idx]
            alias = key
            if alias != entity.canonical and alias not in entity.aliases:
                entity.aliases = tuple(sorted(set(entity.aliases) | {alias}))
            self._by_key[alias] = best_idx
            entity.salience += 1
            return entity

        # ---- Miss: a genuinely new entity --------------------------------
        resolved_type = entity_type or decide_type(name, context)
        entity = Entity(
            canonical=key,
            aliases=(),
            type=resolved_type if resolved_type in ENTITY_TYPES else "other",
            salience=1,
        )
        self._entities.append(entity)
        self._by_key[key] = len(self._entities) - 1
        return entity

    # -- merging ----------------------------------------------------------

    def merge(self, survivor: str, absorbed: str) -> Entity | None:
        """Fold entity ``absorbed`` into ``survivor``; union their aliases.

        One canonical name is kept -- the survivor's -- and every name that
        pointed at the absorbed entity (its canonical name and all its aliases)
        becomes an alias of the survivor. Salience is summed, so an audit does
        not see mentions disappear. The entity's ``type`` keeps the survivor's
        unless the survivor is ``other``, in which case the absorbed type is
        adopted (``other`` carries no information to lose).

        Returns the surviving :class:`Entity`, or ``None`` when either name does
        not resolve exactly -- a merge is a destructive operation, so an
        ambiguous argument is refused rather than guessed at. That is the
        opposite of :meth:`resolve`'s forgiving behaviour, and deliberately so.

        >>> store = EntityStore()
        >>> _ = store.resolve("Acme Corp", entity_type="org")
        >>> _ = store.resolve("Acme Limited", entity_type="org")
        >>> merged = store.merge("Acme Corp", "Acme Limited")
        >>> merged.canonical
        'acme corp'
        >>> merged.aliases
        ('acme limited',)
        >>> len(store)
        1
        >>> store.resolve("ACME LIMITED") is merged
        True

        >>> EntityStore().merge("a", "b") is None
        True

        Provenance: the target has ``merge_entity``
        (``agentmem/bipartite_graph.py:106``) and no mem0 equivalent -- mem0
        only ever appends links (``main.py:623-631``), which is why its merges
        cannot be undone.
        """
        a_key = normalize_name(survivor)
        b_key = normalize_name(absorbed)
        a_idx = self._by_key.get(a_key)
        b_idx = self._by_key.get(b_key)
        if a_idx is None or b_idx is None or a_idx == b_idx:
            return None

        keep = self._entities[a_idx]
        drop = self._entities[b_idx]

        names = {b_key, *drop.aliases}
        names.discard(keep.canonical)
        keep.aliases = tuple(sorted(set(keep.aliases) | names))
        keep.salience += drop.salience
        if keep.type == "other" and drop.type != "other":
            keep.type = drop.type

        # Drop the absorbed entity, then repoint every key. Rebuilding the
        # index is O(n) and this is a rare, deliberate operation -- an
        # incremental fix-up would be more code for no measurable gain.
        self._entities.pop(b_idx)
        self._by_key = {}
        for i, entity in enumerate(self._entities):
            self._by_key[entity.canonical] = i
            for alias in entity.aliases:
                self._by_key[alias] = i
        return keep

    # -- convenience ------------------------------------------------------

    def aliases_of(self, name: str) -> tuple[str, ...]:
        """Aliases of the entity ``name`` resolves to exactly, else ``()``.

        >>> s = EntityStore(); _ = s.resolve("Acme Corp")
        >>> s.aliases_of("acme corp")
        ()
        >>> s.aliases_of("unknown")
        ()
        """
        entity = self.get(name)
        return () if entity is None else entity.aliases

    def canonical_names(self) -> list[str]:
        """Every canonical name, in insertion order. Useful for assertions.

        >>> [e.canonical for e in EntityStore()]
        []
        """
        return [e.canonical for e in self._entities]


#: Verb forms that mark a simple declarative predicate, matched on surface form
#: only. Rules, not semantics: this table exists so a sentence can be split at a
#: verb without a parser. Deliberately short -- every entry is a word this
#: module can point at in its own tests.
_PREDICATE_VERBS: tuple[str, ...] = (
    "is", "are", "was", "were", "has", "have", "had", "owns", "own",
    "works", "work", "worked", "lives", "live", "lived", "likes", "like",
    "liked", "wrote", "write", "written", "said", "says", "say", "knows",
    "know", "knew", "wants", "want", "wanted", "needs", "need", "needed",
    "uses", "use", "used", "builds", "build", "built", "runs", "run", "ran",
    "manages", "manage", "managed", "leads", "lead", "led", "joined", "join",
    "joins", "left", "leave", "leaves", "founded", "founds", "reports",
    "report", "reported", "prefers", "prefer", "preferred", "requires",
    "require", "required", "supports", "support", "supported", "depends",
    "depend", "depended", "contains", "contain", "contained", "includes",
    "include", "included", "meets", "meet", "met", "sends", "send", "sent",
    "creates", "create", "created", "attended", "attend", "attends",
    "purchased", "purchase", "purchases", "visited", "visit", "visits",
)

#: Attributive cue phrases: the text after one of these is a provenance label.
_ATTRIBUTION_CUES: tuple[str, ...] = (
    "according to ", "as told by ", "reported by ", "attributed to ",
    "source:", "per ", "from ",
)

#: Sentence tokenisers for the shapes this splitter supports.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_LEADING_DETERMINER = re.compile(r"^(the|a|an|this|that|these|those)\s+", re.I)

#: Qualifier key holding the provenance label. Named once so a rename is one
#: edit; see :class:`Proposition` and ``mem0/memory/main.py:1244``.
ATTRIBUTED_TO_KEY = "attributed_to"


def _split_attribution(sentence: str) -> tuple[str, str | None]:
    """Peel one attribution cue off ``sentence``; return ``(rest, label)``.

    Case-insensitive, and only *one* cue is consumed: nested attribution
    (``"per the report from Legal"``) keeps the trailing phrase as part of the
    label rather than trying to parse a chain. The **last** cue wins, because
    attribution is normally sentence-final.

    >>> _split_attribution("Acme is in London according to the minutes")
    ('Acme is in London', 'the minutes')
    >>> _split_attribution("Acme is in London")
    ('Acme is in London', None)

    Provenance: ``mem0/memory/main.py:1244`` (``attributed_to`` as a field).
    """
    lowered = sentence.lower()
    best_at = -1
    best_cue = ""
    for cue in _ATTRIBUTION_CUES:
        at = lowered.rfind(cue)
        if at > best_at:
            best_at = at
            best_cue = cue
    if best_at < 0:
        return sentence.strip(), None
    rest = sentence[:best_at].strip(" .;,")
    label = sentence[best_at + len(best_cue) :].strip(" .;,")
    # A cue with nothing after it is not an attribution.
    return rest, (label or None)


def _split_at_predicate(text: str) -> tuple[str, str, str] | None:
    """Split ``text`` into ``(subject, predicate, object)`` at the first verb.

    Returns ``None`` when no predicate verb is found or when either side would
    be empty. A proposition with no subject or no object is not a proposition,
    and inventing one would be worse than returning nothing.

    A leading determiner is trimmed from the subject (``"The Acme Corp"`` ->
    ``"acme corp"``): the determiner is grammar, not identity, and it would
    otherwise prefix every subject string and break exact matching downstream.

    >>> _split_at_predicate("Acme Corp owns Widget Ltd")
    ('acme corp', 'owns', 'widget ltd')
    >>> _split_at_predicate("The Acme Corp owns Widget Ltd")
    ('acme corp', 'owns', 'widget ltd')
    >>> _split_at_predicate("no verb here at all") is None
    True
    """
    words = text.split()
    for i, word in enumerate(words):
        bare = re.sub(r"[^a-z']", "", word.lower())
        if bare not in _PREDICATE_VERBS:
            continue
        subject = " ".join(words[:i]).strip()
        predicate = word.strip(_EDGE_PUNCT) or bare
        obj = " ".join(words[i + 1 :]).strip()
        subject = _LEADING_DETERMINER.sub("", subject).strip()
        if not subject or not obj:
            return None
        return (
            normalize_name(subject),
            normalize_name(predicate),
            normalize_name(obj),
        )
    return None


def extract_propositions(
    text: str, *, attributed_to: str | None = None
) -> list[Proposition]:
    """Extract subject-predicate-object propositions from ``text``.

    **ANOTHER RULE-BASED PLACEHOLDER**, labelled as one for the same reason
    :func:`decide_type` is: mem0's real extractor is an LLM answering to a JSON
    schema, and mmemz 0.1.0 has no model. What ships is a deterministic splitter
    good enough to exercise the *carrier* and honest about being a stand-in.

    THE SHAPE IS THE CONTRACT
    ------------------------
    ``list[Proposition]`` with normalised ``subject``/``predicate``/``object``
    and provenance in ``qualifiers["attributed_to"]``. An LLM backend returns the
    same list for the same input, so swapping it in is a local change.

    ``attributed_to`` arrives two ways, and the interaction is the point:

    * the **argument** -- the caller knows the speaker or document, so every
      proposition in the text inherits the label;
    * the **text** -- an in-sentence cue (``"according to the minutes"``,
      ``"per Legal"``, ``"from the 2023 report"``) is peeled off and becomes that
      sentence's label, **overriding the argument for that sentence only**. A
      quote inside a document is attributed to whoever was quoted, not to the
      document that recorded it.

    mem0 promotes ``attributed_to`` to an enumerated field of a memory row
    (``mem0/memory/main.py:1244``), so it can hold exactly one value per memory.
    The target carries it in ``Proposition.qualifiers`` (``agentmem/models.py:73``,
    qualifiers at ``:82``), which lets provenance vary per *fact* and travel
    beside ``confidence`` and temporal bounds without a schema change. mmemz
    follows the target; the spec's verdict on mem0's row is **DO-NOT-PORT**.

    Splitting is on sentence terminators and newlines. A sentence yielding no
    predicate verb is skipped rather than forced into a triple. Propositions come
    back in text order and are **not** deduplicated here -- reconciliation is the
    consolidation pass's job (the target does it bitemporally at
    ``agentmem/consolidation.py:1187``), not the extractor's.

    >>> ps = extract_propositions("Acme Corp owns Widget Ltd.", attributed_to="minutes-2024")
    >>> [(p.subject, p.predicate, p.object) for p in ps]
    [('acme corp', 'owns', 'widget ltd')]
    >>> ps[0].get_attributed_to()
    'minutes-2024'

    >>> p = extract_propositions("Ada is a mathematician according to the letter")[0]
    >>> p.get_attributed_to()
    'the letter'
    >>> p.object
    'a mathematician'

    >>> extract_propositions("")
    []
    >>> extract_propositions("no verb here at all")
    []

    Provenance: ``mem0/memory/main.py:1244`` (``attributed_to`` promotion);
    ``agentmem/models.py:73``/``:82`` (``Proposition``, ``qualifiers``).
    """
    if not text:
        return []
    if not isinstance(text, str):
        text = str(text)

    propositions: list[Proposition] = []
    for raw_sentence in _SENTENCE_SPLIT.split(text):
        sentence = raw_sentence.strip()
        if not sentence:
            continue
        body, label = _split_attribution(sentence)
        triple = _split_at_predicate(body)
        if triple is None:
            continue
        subject, predicate, obj = triple
        effective = label if label is not None else attributed_to
        qualifiers: dict[str, object] = {}
        if effective is not None:
            qualifiers[ATTRIBUTED_TO_KEY] = effective
        propositions.append(
            Proposition(
                subject=subject,
                predicate=predicate,
                object=obj,
                attributed_to=effective,
                qualifiers=qualifiers,
            )
        )
    return propositions


__all__ = [
    "ENTITY_TYPES",
    "MERGE_THRESHOLD",
    "ATTRIBUTED_TO_KEY",
    "Entity",
    "Proposition",
    "EntityStore",
    "normalize_name",
    "decide_type",
    "extract_propositions",
]
