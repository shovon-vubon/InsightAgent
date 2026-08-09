import { NotImplemented } from "@/components/not-implemented";

export default function KnowledgePage() {
  return (
    <NotImplemented
      title="Knowledge base"
      phase="Phase 3"
      summary="Upload documents, watch them being ingested, and inspect the chunks the agent can retrieve."
      delivers={[
        "Upload for PDF, DOCX, TXT, CSV, and XLSX with validation and size limits",
        "Ingestion status: UPLOADED, PROCESSING, READY, FAILED",
        "Chunk counts, page ranges, and extracted metadata per document",
        "Delete, which also removes the document's embeddings",
      ]}
    />
  );
}