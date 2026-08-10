"use client";

import { useQuery } from "@tanstack/react-query";

import { AskPanel } from "@/features/knowledge/ask-panel";
import { DocumentList } from "@/features/knowledge/document-list";
import * as documents from "@/services/documents";

function TestDoubleBanner() {
  return (
    <div className="rounded border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900">
      <strong className="font-semibold">Deterministic test embedder.</strong> Vectors come from a
      hashing bag-of-words function, not a model: retrieval is lexical, so results prove the
      pipeline works but say nothing about retrieval quality. Set{" "}
      <code className="font-mono">EMBEDDING_PROVIDER=ollama</code> in{" "}
      <code className="font-mono">.env</code> and run{" "}
      <code className="font-mono">ollama pull nomic-embed-text</code> for real embeddings.
    </div>
  );
}

function StatsBar() {
  const { data: stats } = useQuery({
    queryKey: ["knowledge-stats"],
    queryFn: documents.getKnowledgeStats,
  });

  if (!stats) return null;

  const items: Array<[string, string]> = [
    ["Documents", `${stats.documents} / ${stats.document_limit}`],
    ["Ready", String(stats.ready)],
    ["Chunks indexed", String(stats.total_chunks)],
    [
      "Storage",
      `${documents.formatBytes(stats.total_bytes)} / ${documents.formatBytes(stats.storage_limit_bytes)}`,
    ],
    ["Embedding model", `${stats.embedding_model} · ${stats.embedding_dimensions}d`],
  ];

  return (
    <div className="space-y-3">
      {stats.is_test_double && <TestDoubleBanner />}
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {items.map(([label, value]) => (
          <div key={label} className="rounded border border-[--color-border] bg-white px-3 py-2">
            <dt className="text-xs text-[--color-muted]">{label}</dt>
            <dd className="mt-0.5 truncate text-sm font-medium text-[--color-ink]">{value}</dd>
          </div>
        ))}
      </dl>
      {stats.failed > 0 && (
        <p className="text-xs text-red-700">
          {stats.failed} document{stats.failed === 1 ? "" : "s"} failed to process.
        </p>
      )}
    </div>
  );
}

export function KnowledgeWorkspace() {
  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-lg font-semibold text-[--color-ink]">Knowledge base</h1>
        <p className="text-sm text-[--color-muted]">
          Upload documents, then ask questions answered strictly from their contents.
        </p>
      </header>

      <StatsBar />
      <DocumentList />
      <AskPanel />
    </div>
  );
}
