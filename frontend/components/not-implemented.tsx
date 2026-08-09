interface NotImplementedProps {
  title: string;
  phase: string;
  summary: string;
  delivers: string[];
}

/**
 * Placeholder for routes whose feature has not been built yet.
 *
 * Stating the phase and the intended scope is deliberate: a screen that quietly
 * renders fake content is exactly the kind of thing the brief warns against
 * (§61.8, §68). Nothing here pretends to work.
 */
export function NotImplemented({ title, phase, summary, delivers }: NotImplementedProps) {
  return (
    <div className="mx-auto max-w-2xl px-8 py-12">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <span className="rounded-full border border-[--color-border] bg-white px-2.5 py-0.5 text-xs text-[--color-ink-muted]">
          Not implemented · {phase}
        </span>
      </div>

      <p className="mt-3 text-sm text-[--color-ink-muted]">{summary}</p>

      <div className="mt-6 rounded-lg border border-[--color-border] bg-white p-5">
        <p className="text-xs font-medium uppercase tracking-wide text-[--color-ink-muted]">
          {phase} will deliver
        </p>
        <ul className="mt-3 flex flex-col gap-2">
          {delivers.map((item) => (
            <li key={item} className="flex gap-2 text-sm">
              <span aria-hidden className="text-[--color-ink-muted]">
                •
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
