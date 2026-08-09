import clsx from "clsx";
import type { InputHTMLAttributes } from "react";
import { useId } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string | undefined;
}

export function Field({ label, error, className, ...props }: FieldProps) {
  const generatedId = useId();
  const id = props.id ?? generatedId;
  const errorId = `${id}-error`;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-[--color-ink]">
        {label}
      </label>
      <input
        {...props}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className={clsx(
          "rounded-md border bg-white px-3 py-2 text-sm text-[--color-ink]",
          "placeholder:text-[--color-ink-muted]",
          error ? "border-[--color-danger]" : "border-[--color-border]",
          className,
        )}
      />
      {error ? (
        <p id={errorId} className="text-xs text-[--color-danger]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
