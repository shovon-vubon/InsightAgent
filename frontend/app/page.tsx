import { redirect } from "next/navigation";

export default function RootPage() {
  // The authenticated layout decides where an unauthenticated visitor lands, so
  // there is exactly one place that owns that redirect.
  redirect("/research");
}
