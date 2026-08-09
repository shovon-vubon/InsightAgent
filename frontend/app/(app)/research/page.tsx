import { NotImplemented } from "@/components/not-implemented";

export default function ResearchPage() {
  return (
    <NotImplemented
      title="Research workspace"
      phase="Phase 7"
      summary="Ask a question and watch the agent plan, choose tools, and assemble a cited answer."
      delivers={[
        "Question input with streamed agent progress over SSE",
        "Live execution states: planning, retrieval, SQL, analysis, verification",
        "Final answer with inline citations, charts, and a confidence breakdown",
        "Conversation history in the sidebar",
      ]}
    />
  );
}