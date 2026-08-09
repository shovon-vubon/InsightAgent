import clsx from "clsx";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  isLoading?: boolean;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-[--color-accent] text-white hover:bg-[--color-accent-hover] disabled:bg-[#9db4e6]",
  secondary:
    "bg-white text-[--color-ink] border border-[--color-border] hover:bg-[--color-surface-muted]",
  ghost: "bg-transparent text-[--color-shell-ink] hover:bg-white/10",
};

export function Button({
  variant = "primary",
  isLoading = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      disabled={disabled ?? isLoading}
      aria-busy={isLoading}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
        "transition-colors disabled:cursor-not-allowed",
        VARIANTS[variant],
        className,
      )}
    >
      {isLoading ? "Working…" : children}
    </button>
  );
}
