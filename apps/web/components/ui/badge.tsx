import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-md font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "bg-foreground text-accent-fg",
        secondary: "bg-muted text-foreground-muted",
        outline: "border border-border text-foreground",
        success: "bg-green-soft text-green-fg border border-green-border",
        warning: "bg-orange-soft text-orange-fg border border-orange-border",
        danger: "bg-red-soft text-red-fg border border-red-border",
        info: "bg-blue-soft text-blue-fg border border-blue-border",
      },
      size: {
        xs: "text-[11px] px-1.5 py-px gap-1",
        sm: "text-xs px-2 py-0.5",
        md: "text-sm px-3 py-1",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "md",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {
  dot?: boolean;
}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant, size, dot = false, children, ...props }, ref) => (
    <span
      ref={ref}
      className={cn(badgeVariants({ variant, size, className }))}
      {...props}
    >
      {dot && (
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      )}
      {children}
    </span>
  ),
);
Badge.displayName = "Badge";

export { badgeVariants };
