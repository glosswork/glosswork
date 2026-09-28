/** Shared fixtures for `/activity`'s two component suites. */
import { http, HttpResponse } from "msw";

import type { AuditEventDoc } from "../api/records";
import type { ObjectTypeDetail, ObjectTypeSummary } from "../api/objectTypes";

export const PROSPECT: ObjectTypeSummary = {
  key: "prospect",
  name: "Prospect",
  description: "A company we might sell to.",
  key_prefix: "PROS",
  record_count: 2,
  field_count: 3,
  your_access: "admin",
};

export const objectTypeDetail: ObjectTypeDetail = {
  ...PROSPECT,
  name_plural: "Prospects",
  display_field_key: null,
  effective_display_field_key: null,
  system_fields: [],
  fields: [
    {
      key: "stage",
      name: "Stage",
      // `single_select`, and `options` at the top level rather than under `config`: the shape
      // `describe_object_type` actually returns (`api/objectTypes.ts`). A fixture with the wrong
      // shape renders the option KEY and looks like a formatting bug in the screen.
      type: "single_select",
      description: "Where the deal has got to.",
      required: false,
      position: 1,
      options: [
        { value: "won", label: "Won", description: "Closed and signed." },
        { value: "lost", label: "Lost", description: "Closed and gone." },
      ],
      is_deleted: false,
    },
    {
      key: "owner",
      name: "Owner",
      type: "user_ref",
      description: "Who is accountable.",
      required: false,
      position: 2,
      config: {},
      is_deleted: false,
    },
  ],
} as unknown as ObjectTypeDetail;

let nextId = 1;

/** One audit event, with the fields this screen actually reads. */
export function auditEvent(overrides: Partial<AuditEventDoc> = {}): AuditEventDoc {
  return {
    id: nextId++,
    ts: "2026-09-10T09:14:00Z",
    request_id: "req-1",
    principal_id: "00000000-0000-4000-8000-000000000001",
    principal_type: "user",
    agent_label_id: null,
    agent_label: null,
    auth_method: "session",
    surface: "ui",
    entity_type: "record",
    entity_id: "record-1",
    record_id: "record-1",
    object_type_id: "type-1",
    action: "update",
    field_key: "stage",
    old_value: "won",
    new_value: "lost",
    note: null,
    principal_display_name: "Dana Reyes",
    record_key: "PROS-005",
    ...overrides,
  } as AuditEventDoc;
}

export function resetEventIds(): void {
  nextId = 1;
}

export const activityHandlers = [
  http.get("/api/v1/object-types", () => HttpResponse.json([PROSPECT])),
  http.get("/api/v1/object-types/:key", () => HttpResponse.json(objectTypeDetail)),
  http.get("/api/v1/principals/directory", () =>
    HttpResponse.json({
      principals: [
        {
          id: "00000000-0000-4000-8000-000000000001",
          display_name: "Dana Reyes",
          email: "dana@example.com",
          type: "user",
          is_active: true,
        },
      ],
    }),
  ),
  http.get("/api/v1/agent-labels/directory", () =>
    HttpResponse.json({
      agent_labels: [
        { id: "label-1", label: "sales-agent", display_name: "Sales Agent" },
        { id: "label-2", label: "claude-code", display_name: null },
      ],
    }),
  ),
];
