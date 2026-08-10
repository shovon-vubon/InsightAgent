"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/services/api-client";
import * as documents from "@/services/documents";
import {
  ACCEPTED_EXTENSIONS,
  IN_PROGRESS,
  type DocumentStatus,
  type KnowledgeDocument,
} from "@/types/documents";

const STATUS_STYLES: Record<DocumentStatus, string> = {
  UPLOADED: "bg-slate-100 text-slate-700",
  PROCESSING: "bg-blue-50 text-blue-700",
  READY: "bg-emerald-50 text-emerald-700",
  FAILED: "bg-red-50 text-red-700",
};

function StatusBadge({ status }: { status: DocumentStatus }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
      // Status changes without user action, so it is announced.
      aria-live="polite"
    >
      {status === "PROCESSING" || status === "UPLOADED" ? "Processing…" : status.toLowerCase()}
    </span>
  );
}

function DocumentRow({
  document,
  onDelete,
  deleting,
}: {
  document: KnowledgeDocument;
  onDelete: (id: string) => void;
  deleting: boolean;
}) {
  const detail: string[] = [documents.formatBytes(document.size_bytes)];
  if (document.page_count !== null) detail.push(`${document.page_count} pages`);
  if (document.status === "READY") detail.push(`${document.chunk_count} chunks`);

  return (
    <li className="flex items-start justify-between gap-4 border-b border-[--color-border] px-4 py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-[--color-ink]">
            {document.title ?? document.filename}
          </span>
          <StatusBadge status={document.status} />
        </div>
        <p className="mt-0.5 truncate text-xs text-[--color-muted]">
          {document.filename} · {detail.join(" · ")}
        </p>
        {document.error && (
          <p className="mt-1 text-xs text-red-700">{document.error}</p>
        )}
      </div>
      <Button
        type="button"
        variant="secondary"
        onClick={() => onDelete(document.id)}
        disabled={deleting}
        aria-label={`Delete ${document.filename}`}
      >
        Delete
      </Button>
    </li>
  );
}

export function DocumentList() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const { data: docs = [], isLoading } = useQuery({
    queryKey: ["documents"],
    queryFn: documents.listDocuments,
    // Ingestion happens on a worker, so the only way to see it finish is to ask.
    // Polling stops as soon as nothing is in flight rather than running forever.
    refetchInterval: (query) => {
      const rows = query.state.data as KnowledgeDocument[] | undefined;
      return rows?.some((row) => IN_PROGRESS.includes(row.status)) ? 1500 : false;
    },
  });

  const upload = useMutation({
    mutationFn: documents.uploadDocument,
    onSuccess: (result) => {
      setUploadError(null);
      setNotice(
        result.duplicate
          ? `"${result.filename}" is already in the knowledge base.`
          : `"${result.filename}" uploaded and queued for processing.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-stats"] });
    },
    onError: (error: unknown) => {
      setNotice(null);
      setUploadError(
        error instanceof ApiError ? error.message : "The upload failed. Please try again.",
      );
    },
  });

  const remove = useMutation({
    mutationFn: documents.deleteDocument,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-stats"] });
    },
  });

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    // Reset so re-selecting the same file fires `change` again.
    event.target.value = "";
  }

  return (
    <section className="rounded-lg border border-[--color-border] bg-white">
      <header className="flex items-center justify-between gap-4 border-b border-[--color-border] px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-[--color-ink]">Documents</h2>
          <p className="text-xs text-[--color-muted]">
            {ACCEPTED_EXTENSIONS.join(", ")} · processed in the background
          </p>
        </div>
        <div>
          <input
            ref={fileInput}
            type="file"
            className="sr-only"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            onChange={handleFile}
          />
          <Button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={upload.isPending}
          >
            {upload.isPending ? "Uploading…" : "Upload document"}
          </Button>
        </div>
      </header>

      {uploadError && (
        <p role="alert" className="border-b border-red-100 bg-red-50 px-4 py-2 text-xs text-red-700">
          {uploadError}
        </p>
      )}
      {notice && (
        <p className="border-b border-[--color-border] px-4 py-2 text-xs text-[--color-muted]">
          {notice}
        </p>
      )}

      {isLoading ? (
        <p className="px-4 py-6 text-sm text-[--color-muted]">Loading…</p>
      ) : docs.length === 0 ? (
        <p className="px-4 py-6 text-sm text-[--color-muted]">
          No documents yet. Upload one to give the agent something to cite.
        </p>
      ) : (
        <ul>
          {docs.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              onDelete={(id) => remove.mutate(id)}
              deleting={remove.isPending && remove.variables === document.id}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
