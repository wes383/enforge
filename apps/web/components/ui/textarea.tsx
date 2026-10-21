import * as React from "react";

import { cn } from "@/lib/utils";

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Grow with the content instead of keeping a fixed row count. */
  autoGrow?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, autoGrow = false, ...props }, ref) => {
    const nodeRef = React.useRef<HTMLTextAreaElement | null>(null);

    const setRef = React.useCallback(
      (node: HTMLTextAreaElement | null) => {
        nodeRef.current = node;
        if (typeof ref === "function") ref(node);
        else if (ref) ref.current = node;
      },
      [ref],
    );

    React.useEffect(() => {
      if (!autoGrow) return;
      const node = nodeRef.current;
      if (!node) return;
      node.style.height = "auto";
      node.style.height = `${node.scrollHeight}px`;
    }, [autoGrow, props.value]);

    return (
      <textarea
        ref={setRef}
        className={cn(
          "w-full rounded-md border border-border bg-surface px-3 py-2.5 text-sm leading-relaxed text-foreground placeholder:text-foreground-faint transition-colors focus:border-border-strong focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50",
          autoGrow && "resize-none overflow-hidden",
          className,
        )}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";
