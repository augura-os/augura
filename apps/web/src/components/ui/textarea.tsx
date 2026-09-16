import * as React from "react";
import { cn } from "../../lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[72px] w-full rounded-md border border-[#e5e5e5] bg-white px-3 py-2 text-sm text-neutral-900",
        "placeholder:text-neutral-400",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-neutral-400",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";
