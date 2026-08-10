"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/services/api-client";
import * as documents from "@/services/documents";
import type { AnswerResult, Citation } from "@/types/documents";

/**
 * Renders the answer with its `[n]` markers turned into references.
 *
 * The markers are the contract between the model and the citation validator, so
 * they are shown rather than stripped: a reader can see which sentence rests on
 * which source, which is the entire point of the citation pipeline.
 */
function AnswerText({ text, citations }: { text: string; citations: Citation[] }) {
  const known = new Set(citations.map((citation) => citation.marker));
  const parts = text.split(/(\[\d+(?:\s*,\s*\d+)*\])/g);

  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap text-[--color-ink]">
      {parts.map((part, index) => {
        const match = /^\[(\d+(?:\s*,\s*\d+)*)\]$/.exec(part);
        const group = match?.[1];
        if (!group) return <span key={index}>{part}</span>;

        const markers = group.split(",").map((value) => Number(value.trim()));
        // Every marker here has already survived server-side validation; this is
        // presentation only, never a second gate.
        if (!markers.every((marker) => known.has(marker))) {
          return <span key={index}>{part}</span>;
        }
        return (
          <sup key={index} className="mx-0.5 font-medium text-[--color-accent]">
            [{markers.join(", ")}]
          </sup>
        );
      })}
    </p>
  );
}

function CitationCard({ citation }: { citation: Citation }) {
  const location = documents.formatLocation(citation);
  return (
    <li className="rounded border border-[--color-border] bg-white p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-semibold text-[--color-accent]">[{citation.marker}]</span>
        <span className="text-xs text-[--color-muted]">
          {(citation.score * 100).toFixed(0)}% match
        </span>
      </div>
      <p className="mt-1 text-xs font-medium text-[--color-ink]">{citation.document_title}</p>
      {location && <p className="text-xs text-[--color-muted]">{location}</p>}
      <blockquote className="mt-2 border-l-2 border-[--color-border] pl-2 text-xs text-[--color-muted]">
        {citation.quote.length > 320 ? `${citation.quote.slice(0, 320)}…` : citation.quote}
      </blockquote>
    </li>
  );
}

function ResultView({ result }: { result: AnswerResult }) {
  if (result.insufficient_evidence) {
    return (
      <div className="rounded border border-amber-200 bg-amber-50 p-4">
        <p className="text-sm text-amber-900">{result.answer}</p>
        <p className="mt-2 text-xs text-amber-800">
          {result.candidates_considered} candidate chunks were considered and none cleared the
          relevance floor. No language model was called.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded border border-[--color-border] bg-white p-4">
        <AnswerText text={result.answer} citations={result.citations} />
      </div>

      {result.invalid_markers.length > 0 && (
        <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800">
          The model cited {result.invalid_markers.length} source
          {result.invalid_markers.length === 1 ? "" : "s"} that {" "}
          {result.invalid_markers.length === 1 ? "does" : "do"} not exist (
          {result.invalid_markers.map((marker) => `[${marker}]`).join(", ")}). They were removed
          from the answer before it reached you.
        </p>
      )}

      {result.citations.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold tracking-wide text-[--color-muted] uppercase">
            Sources
          </h3>
          <ul className="space-y-2">
            {result.citations.map((citation) => (
              <CitationCard key={citation.chunk_id} citation={citation} />
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-[--color-muted]">
        {result.provider}/{result.model}
        {result.is_test_double && " (test double)"} · retrieval {result.retrieval_ms.toFixed(0)} ms ·
        total {result.total_ms.toFixed(0)} ms · {result.input_tokens}/{result.output_tokens} tokens
        {result.cost_usd !== null && ` · $${result.cost_usd}`}
      </p>
    </div>
  );
}

export function AskPanel() {
  const [question, setQuestion] = useState("");

  const ask = useMutation({
    mutationFn: (value: string) => documents.askKnowledgeBase(value),
  });

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (trimmed) ask.mutate(trimmed);
  }

  return (
    <section className="space-y-4">
      <form onSubmit={handleSubmit} className="rounded-lg border border-[--color-border] bg-white p-4">
        <label htmlFor="kb-question" className="text-sm font-semibold text-[--color-ink]">
          Ask your documents
        </label>
        <p className="mt-0.5 mb-3 text-xs text-[--color-muted]">
          Answers are drawn only from documents marked READY, and every claim is cited.
        </p>
        <div className="flex gap-2">
          <input
            id="kb-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="What were the main drivers of the Q2 revenue decline?"
            className="flex-1 rounded border border-[--color-border] px-3 py-2 text-sm outline-none focus:border-[--color-accent]"
          />
          <Button type="submit" disabled={ask.isPending || !question.trim()}>
            {ask.isPending ? "Searching…" : "Ask"}
          </Button>
        </div>
      </form>

      {ask.isError && (
        <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800">
          {ask.error instanceof ApiError ? ask.error.message : "The request failed."}
        </p>
      )}

      {ask.isSuccess && <ResultView result={ask.data} />}
    </section>
  );
}
