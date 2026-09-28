"""CSV import and export (FR-E1 through FR-E5, PRD.md section 6.11).

Sits over ``RecordService`` and ``SchemaService`` only (DD-3): no raw SQL, no
row-shaping logic belongs in the route layer. Import is two-phase:

1. A read-only validation pass determines, for every row, whether it creates
   or updates and whether it validates cleanly, collecting every row's errors
   without writing anything. It also tracks what earlier rows in the same file
   have claimed on each unique field, because live state cannot show it a batch
   that has not been written yet.
2. If the caller asked for a dry run, or validation found any error anywhere
   in the batch, nothing is written. A dry run reports the counts the commit
   would produce; an error case reports zero, because a commit with any row
   error writes nothing.
3. Otherwise a write pass applies every row in order, inside **one**
   transaction taken from ``RecordService.write_batch``, so a row failing at write
   time leaves no row before it on disk, which is what FR-E2 requires.
"""

from __future__ import annotations

import csv as csv_module
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from glosswork.actor import ActorContext
from glosswork.errors import NotFoundError, ValidationFailedError
from glosswork.fieldtypes import coerce_value, option_values, validate_value
from glosswork.repositories.models import FieldDef
from glosswork.services.records import MAX_QUERY_LIMIT, RecordService
from glosswork.services.schema import SchemaService

# Field types rendered "as-is" as text on both import and export (docs/DATA_MODEL.md
# section 4): no textual coercion needed, just the schema engine's own value
# validation (string type, max_length, URL pattern). ``user_ref`` is not called out
# explicitly in the CSV cell-format table but is stored as a plain string (a
# principal id), so it is treated the same way here.
_AS_IS_TYPES = frozenset({"short_text", "long_text", "url", "user_ref"})

# Field types converted from CSV text via the schema engine's own textual-coercion
# matrix (the same one used for field type changes), rather than bespoke parsing.
_COERCED_TYPES = frozenset({"integer", "decimal", "date", "datetime", "boolean"})

_NEW_OPTION_DESCRIPTION = "Created from CSV import."

# ------------------------------------------------- formula neutralization (DD-20)
#
# A spreadsheet evaluates a cell that *starts* with one of these, so an exported
# ``=HYPERLINK(...)`` runs when the analyst opens the file. Export prefixes one
# apostrophe; import strips one back. The pair is written together and neither is
# correct without the other.
_FORMULA_TRIGGERS = "=+-@\t\r"

# Prefix when the rendered cell already starts with any run of apostrophes followed by
# a trigger; strip when it starts with a non-empty run. Counting the run is what makes
# the escape injective: prefixing only on a bare trigger would export a stored
# ``'=foo`` verbatim, import would strip it to ``=foo``, and two distinct stored values
# would collapse into one. Under the run-length rule ``=foo`` -> ``'=foo`` ->
# ``''=foo``, each stripping back exactly.
#
# Written literally rather than interpolated from ``_FORMULA_TRIGGERS`` so the pattern
# reads as a pattern; a test pins the two against that constant in both directions.
_NEEDS_ESCAPE = re.compile(r"^'*[=+\-@\t\r]")
_HAS_ESCAPE = re.compile(r"^'+[=+\-@\t\r]")

# The field types that can carry a formula, checked against ``fieldtypes.FIELD_TYPES``
# by name. ``integer`` and ``decimal`` are excluded because ``-5`` is a number
# and prefixing it would break the numeric round trip. ``date``, ``datetime`` and
# ``boolean`` render from typed values. ``url`` and ``user_ref`` are fences rather than
# members: ``_URL_PATTERN`` enforces ``^https?://[^\s]+$`` and a ``user_ref`` value is a
# bare principal id, so neither can begin with a trigger. The ``key`` column and
# relation cells never reach ``_render_cell`` and are record keys.
_GUARDED_TYPES = frozenset({"short_text", "long_text", "single_select", "multi_select"})


@dataclass(slots=True)
class RowError:
    row: int
    field: str | None
    reason: str


@dataclass(slots=True)
class CsvImportResult:
    dry_run: bool
    created: int
    updated: int
    errors: list[RowError]


@dataclass(slots=True)
class _RowPlan:
    """What a validated row will do in the write pass."""

    is_update: bool
    ref: str | None
    values: dict[str, Any]
    relations: dict[str, list[str]]


class CsvService:
    """Both directions are bounded (DD-18), and differently: import has a ceiling on its
    size, and export has one that refuses rather than truncating, because a literal
    export limit of 100,000 that silently truncated would hand back a partial file with
    nothing saying so. ``None`` on any of the three means unbounded and exists for the
    direct-construction call sites in the test suite; ``build_services`` always passes
    the settings."""

    def __init__(
        self,
        records: RecordService,
        schema: SchemaService,
        max_import_bytes: int | None = None,
        max_import_rows: int | None = None,
        max_export_rows: int | None = None,
    ) -> None:
        self._records = records
        self._schema = schema
        self._max_import_bytes = max_import_bytes
        self._max_import_rows = max_import_rows
        self._max_export_rows = max_export_rows

    # ------------------------------------------------------------------ import

    def import_csv(
        self,
        actor: ActorContext,
        object_type_key: str,
        csv_text: str | bytes,
        mode: Literal["create", "upsert"],
        upsert_key: str | None = None,
        dry_run: bool = False,
        create_missing_options: bool = False,
    ) -> CsvImportResult:
        # `write` on the type to import, `read` to export. Declared here
        # at the entry point rather than left to the per-row `create_record` calls, so a
        # `dry_run` import -- which writes nothing and would otherwise slip through --
        # is refused on the same terms as a live one.
        #
        # First, and ahead of the two size ceilings: a caller who may not write this type
        # should be told that, not told their file is too big. Authorization before
        # anything the payload can influence is the ordering, even when the cheaper check
        # is the size one.
        self._records.require_type_level(actor, object_type_key, "write")
        # CSV parsing lives here, not in the route (DD-3): the route is a thin
        # adapter that only reads the upload's raw bytes, bounded. Decoding is part of
        # parsing and belongs here for the same reason -- a ``.decode("utf-8")`` in the
        # route would turn a Latin-1 file into a 500.
        text_body = self._decode(csv_text)
        rows = list(csv_module.DictReader(io.StringIO(text_body)))
        # Both caps refuse **before** any row is validated or written, which is what
        # makes an over-sized import cost nothing rather than cost a partial pass.
        if self._max_import_rows is not None and len(rows) > self._max_import_rows:
            raise ValidationFailedError(
                f"CSV has {len(rows)} data rows; this deployment imports at most "
                f"{self._max_import_rows} in one call (GW_MAX_CSV_IMPORT_ROWS). Split the "
                "file, or ask an administrator to raise the limit.",
                rows=len(rows),
                max_rows=self._max_import_rows,
                setting="GW_MAX_CSV_IMPORT_ROWS",
            )
        object_type, field_list = self._schema.get_object_type(actor, object_type_key)
        fields_by_key = {f.key: f for f in field_list}
        unique_field = self._require_upsert_key(object_type_key, fields_by_key, mode, upsert_key)

        known_columns = {"key"} | set(fields_by_key)
        header_columns: set[str] = set()
        for row in rows:
            header_columns.update(row)
        unknown_columns = sorted(header_columns - known_columns)
        if unknown_columns:
            raise ValidationFailedError(
                f"CSV header has unknown column(s) {unknown_columns} for object type "
                f"{object_type_key!r}. Valid columns: 'key', "
                f"{sorted(fields_by_key)}."
            )
        # An attachment field is a *known* column -- it is in ``fields_by_key``, so it
        # clears the check above -- and ``_build_row`` then skips it, so without this
        # check a populated ``files`` column would import with ``errors: []`` and the ids
        # silently gone. Refused at the header rather than per row because the
        # fault is the file's shape and is identical on every row, and refused
        # unconditionally rather than only when a cell is non-empty: a rule that accepts
        # the file today and refuses it tomorrow, when somebody fills a cell, is not one
        # anybody can learn. ``_build_row``'s skip stays as the funnel-level guarantee
        # DD-12 relies on; this check just makes it audible.
        attachment_columns = sorted(
            column
            for column in header_columns
            if (field := fields_by_key.get(column)) is not None and field.type == "attachment"
        )
        if attachment_columns:
            raise ValidationFailedError(
                f"CSV carries no attachment data in either direction, so column(s) "
                f"{attachment_columns} cannot be imported for object type "
                f"{object_type_key!r}. Remove the column from the file; upload the files "
                f"themselves to POST /api/v1/attachments and reference the returned ids "
                f"with a record write.",
                columns=attachment_columns,
                object_type_key=object_type_key,
            )

        errors: list[RowError] = []
        plans: list[_RowPlan] = []
        # Newly-seen option values per single_select/multi_select field, in first-seen
        # order, only populated when create_missing_options is set (row 2 = the first
        # data row; row 1 is the header, matching a spreadsheet's row numbering).
        new_options: dict[str, list[str]] = {}
        # What earlier rows in *this file* have already claimed on each unique
        # field, as (field key, value) -> (row number, the record that row resolved to).
        # ``_resolve_existing`` queries live state, and during validation none of this
        # batch is written yet, so without this two rows carrying the same unique value
        # both plan a create and the second one only fails at write time. That failure is
        # now safe (the batch rolls back) but it would still be unpredicted, and a dry run
        # that reports no errors for a file that cannot commit is the defect FR-E2 names.
        claimed: dict[tuple[str, str], tuple[int, str | None]] = {}

        for index, row in enumerate(rows):
            row_num = index + 2
            values, relations, row_errors = self._build_row(
                actor, field_list, row, row_num, create_missing_options, new_options
            )
            existing_ref = self._resolve_existing(
                actor, object_type_key, mode, upsert_key, unique_field, row, values
            )
            # Mirrors ``RecordService._check_unique``: only fields marked unique, and
            # only where the row actually carries a value for one.
            for field in field_list:
                if not field.is_unique or field.key not in values:
                    continue
                claim = (field.key, repr(values[field.key]))
                previous = claimed.get(claim)
                if previous is None:
                    claimed[claim] = (row_num, existing_ref)
                    continue
                previous_row, previous_ref = previous
                # Two rows updating the *same* existing record is last-one-wins and has
                # always been allowed; anything else is two records competing for one
                # value, which the unique index will refuse.
                if existing_ref is not None and existing_ref == previous_ref:
                    continue
                row_errors.append(
                    RowError(
                        row_num,
                        field.key,
                        f"Field {field.key!r} must be unique, and row {previous_row} of "
                        f"this file already claims {values[field.key]!r}.",
                    )
                )
            if existing_ref is None:
                missing = [
                    f.key
                    for f in field_list
                    if f.is_required
                    and f.type != "relation"
                    and f.key not in values
                    and f.default_value is None
                ]
                row_errors.extend(
                    RowError(row_num, key, f"Field {key!r} is required and no value was given.")
                    for key in missing
                )
            if row_errors:
                errors.extend(row_errors)
                continue
            plans.append(
                _RowPlan(
                    is_update=existing_ref is not None,
                    ref=existing_ref,
                    values=values,
                    relations=relations,
                )
            )

        # ``created``/``updated`` mean one thing on both branches: what the commit
        # does. A commit with any row error writes nothing (FR-E2), so zero is the honest
        # report for a failed live import *and* the honest prediction for the dry run of
        # one. Only a clean dry run has something to predict, and ``plans`` already holds
        # it -- computing the count here and throwing it away would let a dry run say
        # "0 would be created" and the commit that followed create two.
        if errors:
            return CsvImportResult(dry_run=dry_run, created=0, updated=0, errors=errors)
        if dry_run:
            return CsvImportResult(
                dry_run=True,
                created=sum(1 for plan in plans if not plan.is_update),
                updated=sum(1 for plan in plans if plan.is_update),
                errors=[],
            )

        # Deliberately outside the batch transaction below, for this reason: this is a
        # *schema* write, it goes through ``SchemaService.update_field``, which must be
        # wrapped from the outside by ``_fan_out_scope`` so index fan-out runs after the
        # writer lock is released, and folding it inside would invert that. It is
        # additive and idempotent, so a batch that rolls back after it leaves an unused
        # option definition and a re-run absorbs it.
        if create_missing_options:
            self._create_missing_options(actor, object_type_key, fields_by_key, new_options)

        created = 0
        updated = 0
        # One transaction for the whole batch, which is what FR-E2's "the commit either
        # fully succeeds or fully rolls back" says. If each of these three methods opened
        # its own, a row failing at write time -- uniqueness is the reachable case, being
        # the one constraint that needs the database to check -- would leave every earlier
        # row committed on disk.
        with self._records.write_batch() as conn:
            for plan in plans:
                if plan.is_update:
                    assert plan.ref is not None
                    record = self._records.update_record_in_txn(
                        conn, actor, plan.ref, plan.values, force=True
                    )
                    updated += 1
                else:
                    record = self._records.create_record_in_txn(
                        conn, actor, object_type_key, plan.values
                    )
                    created += 1
                for field_key, target_keys in plan.relations.items():
                    self._records.link_records_in_txn(
                        conn, actor, record.key, field_key, target_keys
                    )

        return CsvImportResult(dry_run=False, created=created, updated=updated, errors=[])

    def _decode(self, body: str | bytes) -> str:
        """Bytes in, text out, with both byte-level refusals raised here.

        The byte cap is measured on the encoded form because that is what the caller
        sent and what ``GW_MAX_CSV_IMPORT_BYTES`` names; a ``str`` caller (the test
        suite, the operator CLI) is measured on its UTF-8 encoding so the two agree.

        Both CSV import ceilings raise ``validation_failed`` rather than the
        ``payload_too_large`` the *edge* cap raises, and that is deliberate: a CSV file's
        size and its row count are properties of the file the caller chose, with one
        remedy between them -- split it -- where the edge cap is a property of the
        transport. Answering the two halves of "this file is too big" with two different
        codes would be the drift, not the consistency.

        A decode failure names the **byte offset**, which is the one piece of
        information that lets someone find the offending cell in a file the server will
        not echo back. Left unhandled, a ``UnicodeDecodeError`` in the route would be a
        500 for an ordinary mistake -- a spreadsheet saved as Latin-1 is not a server
        fault.
        """
        raw = body.encode("utf-8") if isinstance(body, str) else body
        if self._max_import_bytes is not None and len(raw) > self._max_import_bytes:
            raise ValidationFailedError(
                f"CSV file is {len(raw)} bytes; this deployment imports at most "
                f"{self._max_import_bytes} in one call (GW_MAX_CSV_IMPORT_BYTES). Split the "
                "file, or ask an administrator to raise the limit.",
                byte_size=len(raw),
                max_bytes=self._max_import_bytes,
                setting="GW_MAX_CSV_IMPORT_BYTES",
            )
        if isinstance(body, str):
            return body
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            offending = raw[exc.start : exc.start + 1]
            raise ValidationFailedError(
                f"CSV file is not valid UTF-8: byte {exc.start} ({offending!r}) could not "
                f"be decoded ({exc.reason}). Re-save the file as UTF-8 and upload it again.",
                byte_offset=exc.start,
                encoding="utf-8",
            ) from None

    def _require_upsert_key(
        self,
        object_type_key: str,
        fields_by_key: dict[str, FieldDef],
        mode: str,
        upsert_key: str | None,
    ) -> FieldDef | None:
        """Validate the whole-import ``upsert_key`` configuration up front: this is a
        caller mistake, not a per-row data problem, so it raises directly."""
        if mode != "upsert":
            return None
        if not upsert_key:
            raise ValidationFailedError("mode='upsert' requires upsert_key.")
        if upsert_key == "key":
            return None
        field = fields_by_key.get(upsert_key)
        if field is None or not field.is_unique:
            raise ValidationFailedError(
                f"upsert_key {upsert_key!r} must be 'key' or a field of "
                f"{object_type_key!r} marked unique."
            )
        return field

    def _build_row(
        self,
        actor: ActorContext,
        field_list: list[FieldDef],
        row: dict[str, str],
        row_num: int,
        create_missing_options: bool,
        new_options: dict[str, list[str]],
    ) -> tuple[dict[str, Any], dict[str, list[str]], list[RowError]]:
        values: dict[str, Any] = {}
        relations: dict[str, list[str]] = {}
        errors: list[RowError] = []
        for field in field_list:
            if field.type == "attachment":
                continue  # attachment fields are not a CSV column
            cell = _restore_cell(field.type, row.get(field.key) or "")
            if field.type == "relation":
                target_keys, relation_errors = self._resolve_relation_targets(
                    actor, field, cell, row_num
                )
                errors.extend(relation_errors)
                if target_keys:
                    relations[field.key] = target_keys
                continue
            if cell == "":
                continue  # absent value: key omitted entirely (docs/DATA_MODEL.md section 4)
            if field.type == "multi_select":
                selected, option_errors = self._resolve_multi_select(
                    field, cell, row_num, create_missing_options, new_options
                )
                errors.extend(option_errors)
                values[field.key] = selected
                continue
            if field.type == "single_select":
                value, error = self._resolve_single_select(
                    field, cell, row_num, create_missing_options, new_options
                )
                if error is not None:
                    errors.append(error)
                else:
                    values[field.key] = value
                continue
            value, error = self._resolve_scalar(field, cell, row_num)
            if error is not None:
                errors.append(error)
            else:
                values[field.key] = value
        return values, relations, errors

    def _resolve_scalar(
        self, field: FieldDef, cell: str, row_num: int
    ) -> tuple[Any, RowError | None]:
        if field.type in _AS_IS_TYPES:
            try:
                return validate_value(field.type, field.config, field.key, cell), None
            except ValidationFailedError as exc:
                return None, RowError(row_num, field.key, exc.message)
        assert field.type in _COERCED_TYPES
        ok, coerced, reason = coerce_value("short_text", field.type, cell.strip(), field.config)
        if not ok:
            return None, RowError(row_num, field.key, reason or f"Invalid value {cell!r}.")
        try:
            return validate_value(field.type, field.config, field.key, coerced), None
        except ValidationFailedError as exc:
            return None, RowError(row_num, field.key, exc.message)

    def _resolve_single_select(
        self,
        field: FieldDef,
        cell: str,
        row_num: int,
        create_missing_options: bool,
        new_options: dict[str, list[str]],
    ) -> tuple[Any, RowError | None]:
        text = cell.strip()
        ok, coerced, reason = coerce_value("short_text", "single_select", text, field.config)
        if ok:
            return coerced, None
        if create_missing_options:
            _remember_new_option(new_options, field.key, text)
            return text, None
        return None, RowError(row_num, field.key, reason or f"Invalid option {text!r}.")

    def _resolve_multi_select(
        self,
        field: FieldDef,
        cell: str,
        row_num: int,
        create_missing_options: bool,
        new_options: dict[str, list[str]],
    ) -> tuple[list[str], list[RowError]]:
        tokens = [t.strip() for t in cell.split("|") if t.strip()]
        valid = set(option_values(field.config))
        errors: list[RowError] = []
        selected: list[str] = []
        for token in tokens:
            if token not in valid:
                if create_missing_options:
                    _remember_new_option(new_options, field.key, token)
                else:
                    errors.append(
                        RowError(
                            row_num,
                            field.key,
                            f"{token!r} is not an existing option. Valid options: "
                            f"{', '.join(sorted(valid))}.",
                        )
                    )
            if token not in selected:
                selected.append(token)
        return selected, errors

    def _resolve_relation_targets(
        self, actor: ActorContext, field: FieldDef, cell: str, row_num: int
    ) -> tuple[list[str], list[RowError]]:
        tokens = [t.strip() for t in cell.split("|") if t.strip()]
        errors: list[RowError] = []
        targets: list[str] = []
        for token in tokens:
            try:
                self._records.get_record(actor, token)
            except NotFoundError:
                errors.append(
                    RowError(
                        row_num,
                        field.key,
                        f"Relation target {token!r} does not exist.",
                    )
                )
                continue
            targets.append(token)
        return targets, errors

    def _resolve_existing(
        self,
        actor: ActorContext,
        object_type_key: str,
        mode: str,
        upsert_key: str | None,
        unique_field: FieldDef | None,
        row: dict[str, str],
        values: dict[str, Any],
    ) -> str | None:
        if mode != "upsert":
            return None
        if upsert_key == "key":
            key_cell = (row.get("key") or "").strip()
            if not key_cell:
                return None
            try:
                return self._records.get_record(actor, key_cell).key
            except NotFoundError:
                return None
        assert unique_field is not None
        if unique_field.key not in values:
            return None
        result = self._records.query_records(
            actor,
            object_type_key,
            filter={"field": unique_field.key, "op": "eq", "value": values[unique_field.key]},
            limit=1,
        )
        return result.records[0]["key"] if result.records else None

    def _create_missing_options(
        self,
        actor: ActorContext,
        object_type_key: str,
        fields_by_key: dict[str, FieldDef],
        new_options: dict[str, list[str]],
    ) -> None:
        for field_key, discovered in new_options.items():
            field = fields_by_key[field_key]
            existing_options = list(field.config.get("options", []))
            existing_values = {str(o["value"]) for o in existing_options}
            additions = [
                {"value": v, "label": v, "description": _NEW_OPTION_DESCRIPTION}
                for v in discovered
                if v not in existing_values
            ]
            if not additions:
                continue
            self._schema.update_field(
                actor,
                object_type_key,
                field_key,
                {"config": {"options": existing_options + additions}},
            )

    # ------------------------------------------------------------------ export

    def export_csv(
        self,
        actor: ActorContext,
        object_type_key: str,
        filter: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        columns: list[str] | None = None,
    ) -> str:
        """The whole export as one string. A thin caller of :meth:`export_csv_stream`,
        so the two cannot produce different bytes."""
        return "".join(self.export_csv_stream(actor, object_type_key, filter, sort, columns))

    def export_csv_stream(
        self,
        actor: ActorContext,
        object_type_key: str,
        filter: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        columns: list[str] | None = None,
    ) -> Iterator[str]:
        """The export as a generator over pages of ``MAX_QUERY_LIMIT``.

        Three properties, worth keeping apart:

        1. **It streams.** Rather than building the whole file in a ``StringIO`` and
           returning one string, it yields the header and then a page of rows at a time,
           the same generator-plus-``StreamingResponse`` shape ``ExportService.stream``
           uses (FR-E5).
        2. **The ceiling is explicit and it refuses.** A limit of 100,000 passed to
           ``query_records`` as a literal would *truncate*: an export of 100,001 records
           would silently return the first 100,000 with nothing saying so. The number is
           ``GW_MAX_CSV_EXPORT_ROWS``, it defaults to 100,000, and over it is
           ``validation_failed`` naming the cap. Streaming bounds memory, not work, so
           without the cap export would be the one unbounded input.
        3. **Relation columns cost one read per (page, field)** rather than one per
           record per field, with no per-target ``get_record`` fan-out inside that
           (:meth:`RecordService.linked_keys_for_page`).

        **The ceiling is checked eagerly, before the first byte is yielded.** Once a
        ``StreamingResponse`` has sent its headers the status can no longer change, so a
        refusal discovered mid-stream would arrive as a truncated 200 -- exactly the
        failure mode this section exists to remove. The first page is therefore fetched
        here, in the method body, and only the yielding happens in the generator, the
        same eager-lookup reasoning ``AttachmentService.stream_download`` records.
        """
        _, field_list = self._schema.get_object_type(actor, object_type_key)
        fields_by_key = {f.key: f for f in field_list}
        header = self._export_header(object_type_key, field_list, fields_by_key, columns)
        relation_keys = [
            c
            for c in header
            if fields_by_key.get(c) is not None and fields_by_key[c].type == "relation"
        ]

        first = self._records.query_records(
            actor, object_type_key, filter=filter, sort=sort, fields="*", limit=MAX_QUERY_LIMIT
        )
        if self._max_export_rows is not None and first.total_count > self._max_export_rows:
            raise ValidationFailedError(
                f"This export matches {first.total_count} records; the limit is "
                f"{self._max_export_rows} (GW_MAX_CSV_EXPORT_ROWS). Narrow the export with "
                "a filter, or ask an administrator to raise the limit.",
                total_count=first.total_count,
                max_rows=self._max_export_rows,
                setting="GW_MAX_CSV_EXPORT_ROWS",
            )

        def _generate() -> Iterator[str]:
            yield _csv_line(header)
            page = first
            while True:
                links = self._records.linked_keys_for_page(
                    actor,
                    object_type_key,
                    [r["id"] for r in page.records],
                    relation_keys,
                )
                for record in page.records:
                    yield _csv_line(
                        self._export_row(record, header, fields_by_key, links.get(record["id"], {}))
                    )
                if page.next_cursor is None:
                    return
                page = self._records.query_records(
                    actor,
                    object_type_key,
                    filter=filter,
                    sort=sort,
                    fields="*",
                    limit=MAX_QUERY_LIMIT,
                    cursor=page.next_cursor,
                )

        return _generate()

    def _export_header(
        self,
        object_type_key: str,
        field_list: list[FieldDef],
        fields_by_key: dict[str, FieldDef],
        columns: list[str] | None,
    ) -> list[str]:
        if columns is None:
            return ["key"] + [f.key for f in field_list if f.type != "attachment"]
        header: list[str] = []
        for col in columns:
            if col == "key":
                header.append(col)
                continue
            field = fields_by_key.get(col)
            if field is None:
                raise ValidationFailedError(
                    f"Unknown column {col!r} for object type {object_type_key!r}. Valid "
                    f"columns: 'key', {sorted(fields_by_key)}."
                )
            if field.type == "attachment":
                continue  # attachment fields are not a CSV column
            header.append(col)
        return header

    def _export_row(
        self,
        record: dict[str, Any],
        header: list[str],
        fields_by_key: dict[str, FieldDef],
        linked_keys: dict[str, list[str]],
    ) -> list[str]:
        """One row's cells. Takes the page's already-resolved relation targets rather
        than fetching per record, and needs no actor at all: the access
        decision was made once per field when the page's links were read."""
        cells: list[str] = []
        for col in header:
            if col == "key":
                cells.append(record["key"])
                continue
            field = fields_by_key[col]
            if field.type == "relation":
                cells.append("|".join(linked_keys.get(field.key, [])))
            else:
                cells.append(
                    _guard_cell(field.type, _render_cell(field.type, record["data"].get(field.key)))
                )
        return cells


def _remember_new_option(new_options: dict[str, list[str]], field_key: str, value: str) -> None:
    seen = new_options.setdefault(field_key, [])
    if value not in seen:
        seen.append(value)


def _csv_line(cells: list[str]) -> str:
    """One CSV row, quoted and terminated exactly as ``csv.writer`` does.

    A one-row writer over a fresh buffer rather than a hand-rolled join, so the bytes a
    streamed export produces are the bytes a buffered one would produce -- including
    the ``\r\n`` terminator the module writes by default.
    """
    buffer = io.StringIO()
    csv_module.writer(buffer).writerow(cells)
    return buffer.getvalue()


def _guard_cell(field_type: str, cell: str) -> str:
    """Neutralize a rendered cell a spreadsheet would otherwise execute.

    Applied to ``_render_cell``'s *result*, so a ``multi_select`` is guarded once on the
    joined cell and never per member: member two of ``a|=b`` is already inert, and
    prefixing it would produce ``a|'=b``, which ``_resolve_multi_select`` splits and
    matches against the option set -- a miss that is a ``RowError`` with
    ``create_missing_options`` off and, with it on, silently creates an option literally
    named ``'=b``.
    """
    if field_type in _GUARDED_TYPES and _NEEDS_ESCAPE.match(cell):
        return "'" + cell
    return cell


def _restore_cell(field_type: str, cell: str) -> str:
    """The inverse of ``_guard_cell``, applied to the raw cell before any resolver sees
    it.

    Not inside the resolvers: ``_resolve_single_select`` coerces against the option set
    and ``_resolve_multi_select`` splits and matches, so a strip placed after them is a
    strip that never runs on the paths that need it most.

    A hand-written CSV whose cell is a deliberate literal ``'=x`` loses one apostrophe.
    That is the stated price of making the pair symmetric.
    """
    if field_type in _GUARDED_TYPES and _HAS_ESCAPE.match(cell):
        return cell[1:]
    return cell


def _render_cell(field_type: str, value: Any) -> str:
    if value is None:
        return ""
    if field_type == "boolean":
        return "true" if value else "false"
    if field_type == "multi_select":
        return "|".join(str(v) for v in value)
    return str(value)
