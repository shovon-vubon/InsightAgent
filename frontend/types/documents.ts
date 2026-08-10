export type DocumentStatus = "UPLOADED" | "PROCESSING" | "READY" | "FAILED";

export interface KnowledgeDocument {
  id: string;
  filename: string;
  title: string | null;
  content_type: string;
  size_bytes: number;
  status: DocumentStatus;
  /** User-safe reason the document failed. Null unless status is FAILED. */
  error: string | null;
  page_count: number | null;
  chunk_count: number;
  ingestion_ms: number | null;
  created_at: string;
  updated_at: string;
}

export interface UploadResult {
  document_id: string;
  filename: string;
  status: DocumentStatus;
  size_bytes: number;
  /** True when identical content was already present; no new document was made. */
  duplicate: boolean;
}

export interface Citation {
  marker: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  filename: string;
  /** The chunk text the claim was drawn from. */
  quote: string;
  /** Cosine similarity to the question, in [0, 1]. */
  score: number;
  page_from: number | null;
  page_to: number | null;
  section_path: string | null;
  char_start: number;
  char_end: number;
}

export interface AnswerResult {
  answer: string;
  citations: Citation[];
  /** True when nothing cleared the retrieval score floor and no model was called. */
  insufficient_evidence: boolean;
  /** Citation ids the model invented. Non-empty means it fabricated a source. */
  invalid_markers: number[];
  candidates_considered: number;
  retrieval_ms: number;
  total_ms: number;
  provider: string;
  model: string;
  is_test_double: boolean;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string | null;
}

export interface KnowledgeStats {
  documents: number;
  ready: number;
  processing: number;
  failed: number;
  total_chunks: number;
  total_bytes: number;
  storage_limit_bytes: number;
  document_limit: number;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimensions: number;
  is_test_double: boolean;
}

export const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"] as const;

/** Statuses that mean the worker is still going, so the UI should keep polling. */
export const IN_PROGRESS: readonly DocumentStatus[] = ["UPLOADED", "PROCESSING"];
