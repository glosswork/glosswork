"""Structural lint of the golden relevance set (DD-33), runnable before any retrieval code
exists.

``tests/golden/search_golden.json`` was authored deliberately ahead of the search service, so that
the floors measure the implementation rather than the other way round. This module checks that
every case is *well-formed for its class* using nothing but the file and SQLite's own FTS5 Porter
tokenizer: a class (D) paraphrase really shares no content stem with its target, a class (C)
identifier really occurs in exactly one live source, a class (A) concept really lives only in the
named comment, a class (H) record really has exactly three matching sources. None of this needs the
model or the search service; ``tests/test_search_golden.py`` is the run that does.

The keyword tokenization used here is the one docs/DATA_MODEL.md section 10 specifies for the
keyword arm (each whitespace word becomes a phrase of its alphanumeric runs; query-side
stopwords are dropped; phrases are OR-joined), so a case that passes this lint is one whose
lexical claims hold under the arm that will judge them.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# The stopword list and the phrase construction are the shipped ones: the lint
# judges the corpus under exactly the keyword arm that will search it, so a change to
# either is a change to what this file proves, not a divergence it hides.
from glosswork.services.search import keyword_phrases
from glosswork.services.search_tuning import QUERY_STOPWORDS

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "search_golden.json"

TEXT_TYPES = frozenset({"short_text", "long_text"})

_RUN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Source:
    id: int
    record: str
    object_type: str
    kind: str  # 'field' | 'comment'
    key: str  # field key or comment ref
    text: str


class Corpus:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.types = {t["key"]: t for t in doc["object_types"]}
        self.records = {r["ref"]: r for r in doc["records"]}
        self.comments = {c["ref"]: c for c in doc["comments"]}
        self.cases = {c["id"]: c for c in doc["cases"]}
        self.sources: list[Source] = []
        self._build_sources()
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute(
            "CREATE VIRTUAL TABLE lint USING fts5(body, tokenize = 'porter unicode61')"
        )
        for s in self.sources:
            self._conn.execute("INSERT INTO lint(rowid, body) VALUES (?, ?)", (s.id, s.text))

    def _build_sources(self) -> None:
        next_id = 1
        for ref, record in self.records.items():
            if record["deleted"]:
                continue
            for field in self.types[record["type"]]["fields"]:
                if not self.eligible(field):
                    continue
                value = record["values"].get(field["key"])
                if isinstance(value, str) and value.strip():
                    self.sources.append(
                        Source(next_id, ref, record["type"], "field", field["key"], value)
                    )
                    next_id += 1
        for ref, comment in self.comments.items():
            record = self.records[comment["record"]]
            if comment["deleted"] or record["deleted"]:
                continue
            self.sources.append(
                Source(next_id, comment["record"], record["type"], "comment", ref, comment["body"])
            )
            next_id += 1

    @staticmethod
    def eligible(field: dict[str, Any]) -> bool:
        """FR-Q3 as amended: text type AND embed; embed defaults to true only for long_text."""
        embed = field.get("embed", field["type"] == "long_text")
        return field["type"] in TEXT_TYPES and bool(embed)

    def matching(self, fts_query: str) -> set[int]:
        rows = self._conn.execute(
            "SELECT rowid FROM lint WHERE lint MATCH ?", (fts_query,)
        ).fetchall()
        return {int(r[0]) for r in rows}

    def matching_any(self, query: str) -> set[int]:
        phrases = keyword_phrases(query)
        return self.matching(" OR ".join(phrases)) if phrases else set()

    def sources_of(self, record_ref: str) -> list[Source]:
        return [s for s in self.sources if s.record == record_ref]

    def source_for(self, record_ref: str, hit: dict[str, Any]) -> Source:
        key = hit["field_key"] if hit["type"] == "field" else hit["comment"]
        found = [s for s in self.sources_of(record_ref) if s.kind == hit["type"] and s.key == key]
        assert len(found) == 1, f"hit source {hit} not a live eligible source of {record_ref}"
        return found[0]

    def live_comment_count(self, record_ref: str) -> int:
        return sum(
            1 for c in self.comments.values() if c["record"] == record_ref and not c["deleted"]
        )


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return Corpus(json.loads(GOLDEN_PATH.read_text()))


def _content_phrases(query: str) -> list[str]:
    return keyword_phrases(query)


# ----------------------------------------------------------------------- structure


def test_thirty_cases_in_ten_classes_with_the_golden_set_counts(corpus: Corpus) -> None:
    cases = corpus.doc["cases"]
    assert len(cases) == 30
    assert len({c["id"] for c in cases}) == 30
    assert Counter(c["class"] for c in cases) == {
        "A": 4,
        "B": 4,
        "C": 4,
        "D": 5,
        "E": 2,
        "F": 3,
        "G": 2,
        "H": 2,
        "I": 2,
        "J": 2,
    }
    positives = [c for c in cases if c["expect"]["record"] is not None]
    negatives = [c for c in cases if c["expect"]["record"] is None]
    assert len(positives) == 27 and len(negatives) == 3
    assert all(c["expect"]["recall_floor_mode"] == "hybrid" for c in positives)
    assert all(c["expect"]["absent_records"] for c in negatives)


def test_every_reference_resolves_and_every_description_is_present(corpus: Corpus) -> None:
    for t in corpus.types.values():
        assert t["description"].strip(), t["key"]
        for f in t["fields"]:
            assert f["description"].strip(), (t["key"], f["key"])
    for record in corpus.records.values():
        fields = {f["key"]: f for f in corpus.types[record["type"]]["fields"]}
        for key, value in record["values"].items():
            assert key in fields, (record["ref"], key)
            field = fields[key]
            if field["type"] == "single_select":
                allowed = {o["value"] for o in field["config"]["options"]}
                assert value in allowed, (record["ref"], key, value)
        assert record["created_by"] in corpus.doc["principals"]
    for comment in corpus.comments.values():
        assert comment["record"] in corpus.records, comment["ref"]
    for case in corpus.cases.values():
        expect = case["expect"]
        for ref in [expect["record"], *expect["absent_records"]]:
            if ref is not None:
                assert ref in corpus.records, (case["id"], ref)
        for key in case["object_types"] or []:
            assert key in corpus.types, (case["id"], key)
        if expect["hit_source"] is not None:
            corpus.source_for(expect["record"], expect["hit_source"])
        if expect["record"] is not None:
            assert not corpus.records[expect["record"]]["deleted"], case["id"]
        assert 1 <= case["limit"] <= 50
        assert set(case["mode_runs"]) <= {"hybrid", "keyword", "semantic"}
        assert case["as_principal"] in corpus.doc["principals"]


# ----------------------------------------------------------------------- per class


def _cases(corpus: Corpus, cls: str) -> list[dict[str, Any]]:
    return [c for c in corpus.doc["cases"] if c["class"] == cls]


def test_class_a_concept_lives_only_in_the_named_comment(corpus: Corpus) -> None:
    for case in _cases(corpus, "A"):
        target = corpus.source_for(case["expect"]["record"], case["expect"]["hit_source"])
        assert target.kind == "comment", case["id"]
        others = {s.id for s in corpus.sources_of(case["expect"]["record"])} - {target.id}
        for phrase in _content_phrases(case["query"]):
            hits = corpus.matching(phrase)
            assert target.id in hits, (case["id"], phrase, "does not match the comment")
            assert not (hits & others), (
                case["id"],
                phrase,
                "also matches another source of the record",
            )


def test_class_b_concept_lives_only_in_the_named_long_text_field(corpus: Corpus) -> None:
    for case in _cases(corpus, "B"):
        target = corpus.source_for(case["expect"]["record"], case["expect"]["hit_source"])
        assert target.kind == "field", case["id"]
        field_type = next(
            f["type"]
            for f in corpus.types[corpus.records[target.record]["type"]]["fields"]
            if f["key"] == target.key
        )
        assert field_type == "long_text", case["id"]
        others = {s.id for s in corpus.sources_of(case["expect"]["record"])} - {target.id}
        for phrase in _content_phrases(case["query"]):
            hits = corpus.matching(phrase)
            assert target.id in hits, (case["id"], phrase)
            assert not (hits & others), (case["id"], phrase)


def test_class_c_identifier_occurs_in_exactly_one_live_source(corpus: Corpus) -> None:
    for case in _cases(corpus, "C"):
        identifiers = [
            w for w in case["query"].split() if "-" in w and any(ch.isdigit() for ch in w)
        ]
        assert len(identifiers) == 1, case["id"]
        phrase = keyword_phrases(identifiers[0])[0]
        hits = corpus.matching(phrase)
        target = corpus.source_for(case["expect"]["record"], case["expect"]["hit_source"])
        assert hits == {target.id}, (case["id"], phrase, hits)
        assert case["expect"]["hard_rank"] == {"keyword": 1, "hybrid": 1}


def test_class_d_paraphrase_shares_no_content_stem_with_its_target(corpus: Corpus) -> None:
    for case in _cases(corpus, "D"):
        record_sources = {s.id for s in corpus.sources_of(case["expect"]["record"])}
        assert record_sources, case["id"]
        for phrase in _content_phrases(case["query"]):
            shared = corpus.matching(phrase) & record_sources
            assert not shared, (case["id"], phrase, "stem-matches the target; not a paraphrase")
        assert case["expect"]["hard_rank"] == {"semantic": 3}
        assert case["expect"]["absent_in"] == ["keyword"]


def test_class_e_opt_in_positive_and_non_opted_in_negative(corpus: Corpus) -> None:
    positive, negative = _cases(corpus, "E")
    target = corpus.source_for(positive["expect"]["record"], positive["expect"]["hit_source"])
    field_type = next(
        f["type"]
        for f in corpus.types[corpus.records[target.record]["type"]]["fields"]
        if f["key"] == target.key
    )
    assert field_type == "short_text", "E1 must hit an opted-in short_text field"
    for phrase in _content_phrases(positive["query"]):
        assert corpus.matching(phrase) == {target.id}, (positive["id"], phrase)

    assert negative["expect"]["record"] is None
    term = negative["query"].lower()
    for phrase in _content_phrases(negative["query"]):
        assert corpus.matching(phrase) == set(), (
            negative["id"],
            "term reachable through an eligible source",
        )
    ref = negative["expect"]["absent_records"][0]
    record = corpus.records[ref]
    non_opted = [
        f
        for f in corpus.types[record["type"]]["fields"]
        if f["type"] == "short_text"
        and not f.get("embed", False)
        and term in str(record["values"].get(f["key"], "")).lower()
    ]
    assert non_opted, "E2's term must sit in a non-opted-in short_text field of the named record"


def test_class_f_filters_are_selective_as_described(corpus: Corpus) -> None:
    f1, f2, f3 = _cases(corpus, "F")
    hits = corpus.matching_any(f1["query"])
    for ref in [f1["expect"]["record"], *f1["expect"]["absent_records"]]:
        assert hits & {s.id for s in corpus.sources_of(ref)}, (f1["id"], ref)
    assert corpus.records[f1["expect"]["record"]]["values"]["status"] == "active"
    assert corpus.records[f1["expect"]["absent_records"][0]]["values"]["status"] != "active"
    assert f1["object_types"] == ["initiative"] and f1["filter"]["field"] == "status"

    assert corpus.live_comment_count(f2["expect"]["record"]) == 0
    assert corpus.live_comment_count(f2["expect"]["absent_records"][0]) >= 1
    assert f2["object_types"] is None and f2["filter"]["field"] == "comment_count"

    target = f3["expect"]["record"]
    assert corpus.records[target]["created_by"] == "second"
    assert [r for r in corpus.records.values() if r["created_by"] == "second"] == [
        corpus.records[target]
    ]
    assert f3["as_principal"] == "second" and f3["filter"] == {
        "field": "created_by",
        "op": "eq",
        "value": "@me",
    }
    competing = [s for s in corpus.sources if s.record != target and "migrat" in s.text.lower()]
    assert len(competing) >= 4 * f3["limit"], (
        "the first pool of 4 x limit rows must be fillable without the target"
    )
    assert f3["expect"]["widened"] is True


def test_class_g_concept_lives_in_exactly_the_two_named_types(corpus: Corpus) -> None:
    g1, g2 = _cases(corpus, "G")
    assert g1["query"] == g2["query"]
    phrase = '"' + " ".join(_RUN.findall(g1["query"])) + '"'
    hit_records = {
        next(s for s in corpus.sources if s.id == i).record for i in corpus.matching(phrase)
    }
    assert hit_records == {g1["expect"]["record"], g2["expect"]["record"]}
    assert corpus.records[g1["expect"]["record"]]["type"] == g1["object_types"][0]
    assert corpus.records[g2["expect"]["record"]]["type"] == g2["object_types"][0]
    assert (
        corpus.records[g1["expect"]["record"]]["type"]
        != corpus.records[g2["expect"]["record"]]["type"]
    )


def test_class_h_record_has_exactly_three_keyword_matching_sources(corpus: Corpus) -> None:
    for case in _cases(corpus, "H"):
        record_sources = {s.id for s in corpus.sources_of(case["expect"]["record"])}
        matched = corpus.matching_any(case["query"]) & record_sources
        kinds = Counter(next(s for s in corpus.sources if s.id == i).kind for i in matched)
        assert len(matched) == 3 and kinds == {"field": 1, "comment": 2}, (case["id"], kinds)
        assert case["expect"]["other_matches"] == 2


def test_class_i_soft_deleted_content_is_the_only_holder_of_its_term(corpus: Corpus) -> None:
    i1, i2 = _cases(corpus, "I")
    assert corpus.records[i1["expect"]["absent_records"][0]]["deleted"] is True
    assert corpus.matching_any("binders") == set()
    deleted_comments = [c for c in corpus.comments.values() if c["deleted"]]
    assert len(deleted_comments) == 1
    assert deleted_comments[0]["record"] == i2["expect"]["absent_records"][0]
    assert not corpus.records[i2["expect"]["absent_records"][0]]["deleted"]
    assert corpus.matching_any("ledger") == set()
    for case in (i1, i2):
        assert set(case["mode_runs"]) == {"hybrid", "keyword", "semantic"}


def test_class_j_query_words_are_absent_literally_but_present_by_stem(corpus: Corpus) -> None:
    for case in _cases(corpus, "J"):
        record_sources = corpus.sources_of(case["expect"]["record"])
        text = " ".join(s.text.lower() for s in record_sources)
        tokens = set(_RUN.findall(text))
        for word in case["query"].split():
            if word.lower() in QUERY_STOPWORDS:
                continue
            stem_hits = corpus.matching('"' + word + '"') & {s.id for s in record_sources}
            assert stem_hits, (case["id"], word, "no stem match on the target")
        morphological = [w for w in case["query"].split() if w.lower() not in tokens]
        assert morphological, (case["id"], "at least one query word must be absent literally")
        assert case["expect"]["hard_rank"] == {"keyword": 5}


def test_c_and_d_decoys_exist_to_attract_the_wrong_arm(corpus: Corpus) -> None:
    """Every class C target has a same-topic decoy without the identifier (shares at least three
    content stems with the target's hit source), and at least three class D queries have a lexical
    decoy sharing at least three content stems with the query."""
    for case in _cases(corpus, "C"):
        target = corpus.source_for(case["expect"]["record"], case["expect"]["hit_source"])
        words = [
            w
            for w in _RUN.findall(target.text)
            if w.lower() not in QUERY_STOPWORDS and not w.isdigit()
        ]
        shared_by_other: Counter[str] = Counter()
        for w in set(words):
            for sid in corpus.matching('"' + w + '"'):
                src = next(s for s in corpus.sources if s.id == sid)
                if src.record != target.record:
                    shared_by_other[src.record] += 1
        assert shared_by_other and max(shared_by_other.values()) >= 3, (
            case["id"],
            "no same-topic decoy",
        )
    lexical_decoys = 0
    for case in _cases(corpus, "D"):
        by_record: Counter[str] = Counter()
        for phrase in _content_phrases(case["query"]):
            for sid in corpus.matching(phrase):
                by_record[next(s for s in corpus.sources if s.id == sid).record] += 1
        if by_record and max(by_record.values()) >= 3:
            lexical_decoys += 1
    assert lexical_decoys >= 3
