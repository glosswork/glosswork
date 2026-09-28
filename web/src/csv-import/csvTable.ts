/**
 * A small, dependency-free RFC4180 CSV parser and serializer (no CSV library is a project
 * dependency; this pair is a deliberate scope decision for FR-U6). Used to read an uploaded
 * file's header/data rows client-side, and to rewrite the
 * header row to field keys before the wizard uploads the corrected CSV.
 *
 * Rules: fields containing a comma, a double quote, or a newline (`\n` or `\r`) are wrapped in
 * double quotes; an embedded double quote is escaped by doubling it (`""`). The parser accepts
 * both `\r\n` and bare `\n` line endings on input; the serializer always emits `\r\n` between
 * rows (RFC4180 canonical, matching Python's `csv` module default used on the backend).
 */

const NEEDS_QUOTING = /["\r\n,]/;

/** Parses RFC4180 CSV text into rows of raw string cells. Returns `[]` for an empty string. A
 * single trailing newline at end of input does not produce a phantom empty row. */
export function parseCsv(text: string): string[][] {
  if (text.length === 0) return [];

  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  const endField = () => {
    row.push(field);
    field = "";
  };
  const endRow = () => {
    endField();
    rows.push(row);
    row = [];
  };

  let i = 0;
  while (i < text.length) {
    const char = text[i];
    if (inQuotes) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += char;
      i += 1;
      continue;
    }
    if (char === '"') {
      inQuotes = true;
      i += 1;
      continue;
    }
    if (char === ",") {
      endField();
      i += 1;
      continue;
    }
    if (char === "\r") {
      if (text[i + 1] === "\n") i += 1;
      endRow();
      i += 1;
      continue;
    }
    if (char === "\n") {
      endRow();
      i += 1;
      continue;
    }
    field += char;
    i += 1;
  }

  // A pending field/row at end of input (no trailing newline) still needs to land; a truly
  // empty tail right after a newline (field === "" and no cells collected yet) does not.
  if (field !== "" || row.length > 0) {
    endRow();
  }

  return rows;
}

function escapeField(field: string): string {
  if (!NEEDS_QUOTING.test(field)) return field;
  return `"${field.replace(/"/g, '""')}"`;
}

/** Serializes rows of string cells into RFC4180 CSV text, quoting fields that need it. */
export function serializeCsv(rows: string[][]): string {
  return rows.map((row) => row.map(escapeField).join(",")).join("\r\n");
}
