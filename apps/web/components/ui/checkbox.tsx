"use client";

import * as React from "react";
import { Check, Minus } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Minimal accessible checkbox built on a native button. We don't pull in
 * @radix-ui/react-checkbox just for this — the only stateful behaviour we
 * need is the indeterminate tri-state, which is one extra prop here.
 */
export interface CheckboxProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> {
  checked?: boolean;
  indeterminate?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}

export const Checkbox = React.forwardRef<HTMLButtonElement, CheckboxProps>(
  (
    {
      className,
      checked = false,
      indeterminate = false,
      onCheckedChange,
      disabled,
      "aria-label": ariaLabel,
      ...props
    },
    ref,
  ) => {
    const isChecked = checked || indeterminate;
    return (
      <button
        type="button"
        role="checkbox"
        aria-checked={indeterminate ? "mixed" : checked}
        aria-label={ariaLabel}
        disabled={disabled}
        ref={ref}
        onClick={(e) => {
          e.stopPropagation();
          onCheckedChange?.(!checked);
        }}
        className={cn(
          "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border border-input bg-background text-primary-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
          isChecked && "border-primary bg-primary",
          className,
        )}
        {...props}
      >
        {indeterminate ? (
          <Minus className="h-3 w-3" strokeWidth={3} />
        ) : checked ? (
          <Check className="h-3 w-3" strokeWidth={3} />
        ) : null}
      </button>
    );
  },
);
Checkbox.displayName = "Checkbox";
