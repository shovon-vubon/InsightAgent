import { NotImplemented } from "@/components/not-implemented";

export default function DatasetsPage() {
  return (
    <NotImplemented
      title="Datasets"
      phase="Phase 6"
      summary="Tabular data the agent can profile, aggregate, and chart through typed analysis operations."
      delivers={[
        "CSV and XLSX upload with column profiling",
        "Inferred column types, null counts, and distinct counts",
        "Row preview and dataset description",
        "Availability to the python_analysis tool",
      ]}
    />
  );
}