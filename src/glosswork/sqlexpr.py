"""The one module that owns json_extract expression text and index DDL.

docs/DATA_MODEL.md section 5 binding constraints:

1. The object type id is inlined as a literal (never a bound parameter) so SQLite can
   prove the query implies each partial index's WHERE clause. It is validated against a
   strict UUID pattern immediately before interpolation, so any future refactor routing
   a non-UUID value here fails loudly instead of becoming an injection surface.
2. The ``json_extract(data, '$.<field_key>')`` text used in compiled filters MUST be
   byte-identical to the text in the index DDL. Both are produced only here.

A third index shape is the **sort composite** (:func:`sort_index_ddl`). See its
docstring for why a per-field index cannot serve a filtered-and-sorted page and what
the measurement showed.
"""

from __future__ import annotations

import hashlib
import re

from glosswork.errors import ValidationFailedError
from glosswork.fieldtypes import KEY_PATTERN

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def validate_uuid(value: str) -> str:
    """Strict UUID gate for literal SQL interpolation. Rejects anything else loudly."""
    if not isinstance(value, str) or not UUID_PATTERN.match(value):
        raise ValidationFailedError(
            f"Expected a canonical lowercase UUID, got {value!r}. Refusing to inline it into SQL."
        )
    return value


def _validate_key(key: str) -> str:
    if not KEY_PATTERN.match(key):
        raise ValidationFailedError(f"Invalid key {key!r} for SQL expression construction.")
    return key


def json_field_expr(field_key: str) -> str:
    """The canonical extraction expression for a user field. Used by filter compilation,
    sorting, and index DDL alike; byte-identical everywhere by construction."""
    return f"json_extract(data, '$.{_validate_key(field_key)}')"


def type_scope_predicate(object_type_id: str) -> str:
    """The canonical partial-index predicate / query scope for one object type."""
    return f"object_type_id = '{validate_uuid(object_type_id)}' AND deleted_at IS NULL"


def id32(object_type_id: str) -> str:
    """The object type's UUID as 32 hex characters, for use inside an index name.

    Hyphens are not legal in an unquoted SQL identifier, so they are stripped. What
    matters is that the result is **fixed width**: an index name that embeds it can be
    followed by a single free-form key and still be injective, which a type *key*
    cannot do because ``KEY_PATTERN`` admits ``_`` on both sides of the join.
    """
    return validate_uuid(object_type_id).replace("-", "")


def index_prefix(object_type_id: str, unique: bool = False) -> str:
    """Every per-field index name for one object type begins with this, and no other
    type's does. The reconciler lists ``sqlite_master`` by it."""
    return f"{'ux_rec' if unique else 'ix_rec'}_{id32(object_type_id)}_"


def index_name(object_type_id: str, field_key: str, unique: bool = False) -> str:
    """``ix_rec_<id32>_<field_key>`` / ``ux_rec_<id32>_<field_key>`` (DD-12).

    **Injective by construction.** An earlier scheme joined the object type *key* and
    the field key with ``_``, and ``KEY_PATTERN`` admits ``_`` inside both, so type
    ``thing_a`` field ``code`` and type ``thing`` field ``a_code`` produced the same
    name. Creation was guarded against an already-taken name, so the second unique field
    silently got no index at all and the unique partial expression index stopped being
    the hard enforcement docs/DATA_MODEL.md section 5 says it is; dropping either one
    dropped the other's. The id is fixed width, so exactly one ``_<field_key>`` can
    follow it and the decomposition is unambiguous.
    """
    return f"{index_prefix(object_type_id, unique)}{_validate_key(field_key)}"


def index_ddl(
    object_type_id: str,
    field_key: str,
    unique: bool = False,
) -> str:
    """Partial expression index DDL for an indexed or unique field (DD-1).

    Creation is unconditional: there is no existence guard.
    With injective names a collision is impossible, so a create that finds the name
    taken is a bug and must fail loudly rather than silently do nothing. Both creators
    are reached only from a reconciler that has already diffed the desired set against
    ``sqlite_master``. See DD-12 for what that guard was hiding.
    """
    kind = "UNIQUE INDEX" if unique else "INDEX"
    name = index_name(object_type_id, field_key, unique)
    return (
        f"CREATE {kind} {name}\n"
        f"  ON records({json_field_expr(field_key)})\n"
        f"  WHERE {type_scope_predicate(object_type_id)}"
    )


def drop_index_by_name_ddl(name: str) -> str:
    """Drop one index **by name**, for either shape.

    Takes the name rather than the parts because every caller is a reconciler dropping
    something it read out of ``sqlite_master`` and cannot always reconstruct -- an index
    left behind by a field that was deleted while a prior version was running, or by a
    prior version's naming scheme, is exactly the drift a declarative reconcile exists
    to clear. Building this string anywhere else would be raw SQL in the service layer
    (DD-2), so the sort reconciler calls this too.
    """
    return f"DROP INDEX IF EXISTS {name}"


# ---------------------------------------------------------------- sort composites
#
# Added from a measurement rather than from a hunch. See
# ``sort_index_ddl`` for the full reasoning; the short version is that a per-field
# index cannot order a filtered page, so SQLite sorts the whole matching set.

SORT_INDEX_PREFIX = "ix_sort"

# How many hex characters of the pair digest ride in a composite's name. Twelve is 48
# bits: the collision it has to be safe against is between two field pairs of one
# object type, which is a set of tens, not a birthday problem worth widening for.
SORT_INDEX_HASH_CHARS = 12


def sort_index_prefix(object_type_id: str) -> str:
    """Every sort composite for one object type begins with this, and no other type's
    does. A prefix of ``ix_sort_<type key>_`` would not hold that: reconciling type
    ``thing`` would enumerate -- and drop -- every composite belonging to type
    ``thing_a``."""
    return f"{SORT_INDEX_PREFIX}_{id32(object_type_id)}_"


def sort_index_name(object_type_id: str, filter_field_key: str, sort_field_key: str) -> str:
    """``ix_sort_<id32>_<h12>_<filter_key>_<sort_key>`` (DD-12).

    Two keys follow the id here and either may contain ``_``, so fixed width is not
    enough on its own: the digest of the ordered pair is what makes the name injective.
    The keys are kept after it for the operator reading ``sqlite_master``, not for
    uniqueness -- nothing parses them back out.
    """
    a = _validate_key(filter_field_key)
    b = _validate_key(sort_field_key)
    digest = hashlib.sha256(f"{a}\x00{b}".encode()).hexdigest()[:SORT_INDEX_HASH_CHARS]
    return f"{sort_index_prefix(object_type_id)}{digest}_{a}_{b}"


def sort_index_ddl(
    object_type_id: str,
    filter_field_key: str,
    sort_field_key: str,
) -> str:
    """A composite partial index serving ``WHERE A = ? ORDER BY B, id``.

    **Why a per-field index is not enough.** The compiler always appends
    ``records.id ASC`` to the ORDER BY, because keyset pagination needs a total order.
    A single-column index on ``B`` therefore cannot supply the ordering: within equal
    ``B`` values it is in rowid order, and ``records.id`` is a UUID with no relation to
    rowid. SQLite's only remaining option is to find every row matching the filter and
    sort them all, which it does even with ``ANALYZE`` and ``STAT4`` statistics present
    (both verified when it was measured).

    **What that cost, measured on the seeded 200,000-record corpus.** A first page of
    50 ``initiative`` records filtered on ``status`` and sorted by ``due_date`` matched
    35,961 rows and took **39.0 ms**, essentially all of it the sort; the same query
    over this composite takes **0.16 ms**. Cost is linear in rows matched (about 1.1 us
    each), so the composite matters exactly when a filter is *not* selective, which is
    what bounds the set of indexes worth creating (see ``LOW_CARDINALITY_TYPES``).

    **One index covers both sort directions.** Built ascending, it also serves
    ``ORDER BY B DESC, id ASC``, because SQLite scans it backwards and the trailing
    ``id`` column is there to settle ties. On the seeded corpus it read straight out of
    the index in both directions; on a small table it instead takes a partial sort,
    reported as ``USE TEMP B-TREE FOR LAST TERM OF ORDER BY``, which sorts only within
    one group of equal ``B`` values rather than the whole matching set. Either plan is
    the win; a bare ``USE TEMP B-TREE FOR ORDER BY`` is the regression, and
    ``tests/test_sort_composite_indexes.py`` asserts exactly that distinction. Rows
    were verified identical to a forced-alternate-plan reference in both directions,
    so this is one index per pair, not two.

    **What it costs to carry.** Three such indexes over 90,000 records built in 0.6 s,
    left the database file size unchanged, and moved record-creation throughput from
    621 to 614 records per second, which is 1.2%.
    """
    name = sort_index_name(object_type_id, filter_field_key, sort_field_key)
    return (
        f"CREATE INDEX {name}\n"
        f"  ON records({json_field_expr(filter_field_key)}, "
        f"{json_field_expr(sort_field_key)}, id)\n"
        f"  WHERE {type_scope_predicate(object_type_id)}"
    )
