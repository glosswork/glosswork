/**
 * A `describe_object_type` fixture whose fields exercise every operator category of
 * docs/MCP_TOOLS.md section 4's "Operators by field type" table: one representative field per
 * row (text-like types share one field since the table gives them identical operators, and
 * likewise integer/decimal and date/datetime). Each field's `operators` array is exactly what a
 * real backend response would carry — the "all types" `is_null`/`is_not_null` pair appended to
 * its type-specific operators, mirroring docs/MCP_TOOLS.md section 5.1's worked `status` example.
 *
 * Reused by `FilterBuilder.test.tsx` and available to later table/card-view tasks that need a
 * realistic object type to mock against.
 */
import type { FieldDoc, ObjectTypeDetail, SystemFieldDoc } from "../../api/objectTypes";

const titleField: FieldDoc = {
  key: "title",
  name: "Title",
  type: "short_text",
  description: "The record's short display title.",
  required: true,
  unique: false,
  indexed: true,
  embed: true,
  default: null,
  config: {},
  position: 0,
  operators: [
    "eq",
    "neq",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "in",
    "is_null",
    "is_not_null",
  ],
  display_eligible: true,
};

const priorityField: FieldDoc = {
  key: "priority",
  name: "Priority",
  type: "integer",
  description: "1 (highest) through 5 (lowest).",
  required: false,
  unique: false,
  indexed: true,
  embed: false,
  default: 3,
  config: {},
  position: 1,
  operators: ["eq", "neq", "gt", "gte", "lt", "lte", "between", "in", "is_null", "is_not_null"],
  display_eligible: true,
};

const isActiveField: FieldDoc = {
  key: "is_active",
  name: "Is Active",
  type: "boolean",
  description: "Whether this record is currently active.",
  required: true,
  unique: false,
  indexed: false,
  embed: false,
  default: true,
  config: {},
  position: 2,
  operators: ["eq", "is_null", "is_not_null"],
  display_eligible: true,
};

const targetDateField: FieldDoc = {
  key: "target_date",
  name: "Target Date",
  type: "date",
  description: "The date this record is targeted to complete by.",
  required: false,
  unique: false,
  indexed: true,
  embed: false,
  default: null,
  config: {},
  position: 3,
  operators: ["eq", "neq", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"],
  display_eligible: true,
};

const statusField: FieldDoc = {
  key: "status",
  name: "Status",
  type: "single_select",
  description: "Current delivery health.",
  required: true,
  unique: false,
  indexed: true,
  embed: false,
  default: "proposed",
  config: {},
  position: 4,
  operators: ["eq", "neq", "in", "not_in", "is_null", "is_not_null"],
  display_eligible: true,
  options: [
    { value: "proposed", label: "Proposed", description: "Not yet funded or sponsored." },
    { value: "active", label: "Active", description: "Funded, sponsored, and being worked." },
    { value: "blocked", label: "Blocked", description: "Work has stopped." },
  ],
};

const tagsField: FieldDoc = {
  key: "tags",
  name: "Tags",
  type: "multi_select",
  description: "Free-form labels for cross-cutting concerns.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: [],
  config: {},
  position: 5,
  operators: ["has_any", "has_all", "has_none", "is_empty", "is_not_empty", "is_null", "is_not_null"],
  display_eligible: true,
  options: [
    { value: "urgent", label: "Urgent", description: "Needs immediate attention." },
    { value: "deprioritized", label: "Deprioritized", description: "Explicitly lowered priority." },
  ],
};

const ownerField: FieldDoc = {
  key: "owner",
  name: "Owner",
  type: "user_ref",
  description: "The principal accountable for this record.",
  required: true,
  unique: false,
  indexed: true,
  embed: false,
  default: null,
  config: {},
  position: 6,
  operators: ["eq", "neq", "in", "is_null", "is_not_null"],
  display_eligible: false,
};

const parentInitiativeField: FieldDoc = {
  key: "parent_initiative",
  name: "Parent Initiative",
  type: "relation",
  description: "The larger initiative this one rolls up into.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 7,
  operators: ["linked_to", "linked_to_any", "has_links", "has_no_links", "is_null", "is_not_null"],
  display_eligible: false,
  target_type_key: "initiative",
  cardinality: "one",
  inverse_field_key: "child_initiatives",
};

const attachmentsField: FieldDoc = {
  key: "supporting_docs",
  name: "Supporting Docs",
  type: "attachment",
  description: "Uploaded files supporting this record.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 8,
  operators: ["is_empty", "is_not_empty", "is_null", "is_not_null"],
  display_eligible: false,
};

export const filterBuilderFields: FieldDoc[] = [
  titleField,
  priorityField,
  isActiveField,
  targetDateField,
  statusField,
  tagsField,
  ownerField,
  parentInitiativeField,
  attachmentsField,
];

export const filterBuilderSystemFields: SystemFieldDoc[] = [
  {
    key: "created_at",
    type: "datetime",
    description: "When the record was created.",
    operators: ["eq", "neq", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null"],
  },
  {
    key: "created_by",
    type: "user_ref",
    description: "The principal who created the record.",
    operators: ["eq", "neq", "in", "is_null", "is_not_null"],
  },
];

export const filterBuilderObjectType: ObjectTypeDetail = {
  key: "initiative",
  name: "Initiative",
  name_plural: "Initiatives",
  description: "A funded, sponsored workstream in the organisation's portfolio.",
  key_prefix: "INIT",
  record_count: 47,
  field_count: filterBuilderFields.length,
  your_access: "admin",
  display_field_key: null,
  effective_display_field_key: null,
  fields: filterBuilderFields,
  system_fields: filterBuilderSystemFields,
};
