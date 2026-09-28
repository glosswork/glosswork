/**
 * The table cell shows a count rather than a literal "(attachment)" that reads the same whether
 * the record holds zero files or five; the count is what makes the two rows differ. With the
 * literal, every case below fails.
 *
 * The `attachment` branch returns before `table` is read at all, so the stub below is only what
 * the prop type requires, since no table instance is needed to exercise it.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { EditableCell, type EditableCellProps } from "./EditableCell";

const field: FieldDoc = {
  key: "files",
  name: "Files",
  type: "attachment",
  description: "Supporting documents.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 0,
  operators: [],
  display_eligible: false,
};

const table = { options: { meta: {} } } as unknown as EditableCellProps["table"];

function renderCell(value: unknown) {
  const record = { key: "INIT-1", version: 1, data: { files: value } } as unknown as RecordDoc;
  return render(<EditableCell field={field} record={record} table={table} />);
}

describe("EditableCell: attachment count", () => {
  it("counts the stored ids", () => {
    renderCell(["a", "b", "c"]);
    expect(screen.getByText("3 files")).toBeInTheDocument();
  });

  it("says zero rather than nothing on a record holding none", () => {
    renderCell([]);
    expect(screen.getByText("0 files")).toBeInTheDocument();
  });

  it("says zero for an absent value too", () => {
    renderCell(undefined);
    expect(screen.getByText("0 files")).toBeInTheDocument();
  });

  it("uses the singular for one", () => {
    renderCell(["a"]);
    expect(screen.getByText("1 file")).toBeInTheDocument();
  });

  it("no longer renders the placeholder literal", () => {
    renderCell(["a", "b"]);
    expect(screen.queryByText("(attachment)")).toBeNull();
  });
});
