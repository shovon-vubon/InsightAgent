import { apiRequest, apiUpload } from "@/services/api-client";
import type {
  AnswerResult,
  KnowledgeDocument,
  KnowledgeStats,
  UploadResult,
} from "@/types/documents";

export async function listDocuments(): Promise<KnowledgeDocument[]> {
  return apiRequest<KnowledgeDocument[]>("/documents");
}

export async function getDocument(id: string): Promise<KnowledgeDocument> {
  return apiRequest<KnowledgeDocument>(`/documents/${id}`);
}

export async function uploadDocument(file: File): Promise<UploadResult> {
  return apiUpload<UploadResult>("/documents", file);
}

export async function deleteDocument(id: string): Promise<void> {
  return apiRequest<void>(`/documents/${id}`, { method: "DELETE" });
}

export async function getKnowledgeStats(): Promise<KnowledgeStats> {
  return apiRequest<KnowledgeStats>("/documents/stats");
}

export async function askKnowledgeBase(
  question: string,
  documentIds?: string[],
): Promise<AnswerResult> {
  return apiRequest<AnswerResult>("/documents/ask", {
    method: "POST",
    body: {
      question,
      ...(documentIds && documentIds.length > 0 ? { document_ids: documentIds } : {}),
    },
  });
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "p. 4", "pp. 4–6", or the section path when the format has no pages. */
export function formatLocation(citation: {
  page_from: number | null;
  page_to: number | null;
  section_path: string | null;
}): string {
  const parts: string[] = [];
  if (citation.page_from !== null) {
    parts.push(
      citation.page_to !== null && citation.page_to !== citation.page_from
        ? `pp. ${citation.page_from}–${citation.page_to}`
        : `p. ${citation.page_from}`,
    );
  }
  if (citation.section_path) parts.push(citation.section_path);
  return parts.join(" · ");
}
