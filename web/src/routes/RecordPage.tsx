import { useParams } from "react-router-dom";
import { useObjectType } from "../hooks/useObjectType";
import { useRecord } from "../hooks/useRecord";
import { RecordDetailView } from "../record-detail/RecordDetailView";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";

/** `attachments` joins `links`. Without it `record.attachments` is absent and
 * the card can only ever show stored ids, never filenames. */
const GET_RECORD_OPTIONS = { include: ["links", "attachments"] };

/** Route component for `/:objectTypeKey/:recordKey`: fetches the object type (for field
 * definitions) and the record (with its links and attachments included) and hands off to the
 * real card/detail view (FR-U2). */
export function RecordPage() {
  const { objectTypeKey, recordKey } = useParams<{ objectTypeKey: string; recordKey: string }>();
  const objectTypeQuery = useObjectType(objectTypeKey);
  const recordQuery = useRecord(recordKey, GET_RECORD_OPTIONS);

  if (objectTypeQuery.isLoading || recordQuery.isLoading) {
    return <Spinner />;
  }

  if (objectTypeQuery.isError || !objectTypeQuery.data || recordQuery.isError || !recordQuery.data) {
    return <EmptyState title="Record not found." />;
  }

  return <RecordDetailView record={recordQuery.data} objectType={objectTypeQuery.data} />;
}
