import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

function warnAsChildLoading(asChild: boolean, loading: boolean) {
  if (process.env.NODE_ENV !== "production" && asChild && loading) {
    console.warn(
      "[Enforge UI Button] `asChild` and `loading` cannot be used together.",
    );
  }
}

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full font-medium leading-none select-none transition-all duration-base ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-accent text-accent-fg hover:bg-accent-hover",
        outline: "bg-surface text-foreground border border-border hover:bg-hover-bg",
        ghost: "bg-transparent text-foreground hover:bg-hover-bg",
        danger: "bg-red-solid text-red-solid-fg hover:bg-red-solid-hover",
        subtle: "bg-hover-bg text-foreground hover:bg-hover-bg-strong",
        link: "bg-transparent text-foreground underline-offset-4 hover:underline rounded-none",
      },
      size: {
        xs: "h-8 px-3 text-xs gap-1.5",
        sm: "h-9 px-4 text-sm",
        md: "h-10 px-5 text-sm",
        lg: "h-12 px-7 text-base",
        icon: "h-10 w-10 p-0",
        "icon-sm": "h-9 w-9 p-0",
        "icon-xs": "h-8 w-8 p-0",
        fab: "h-[60px] w-[60px] p-0 shadow-fab",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "md",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant,
      size,
      asChild = false,
      loading = false,
      children,
      disabled,
      ...props
    },
    ref,
  ) => {
    warnAsChildLoading(asChild, loading);

    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...(!asChild ? { disabled: disabled || loading } : {})}
        {...props}
      >
        {asChild ? (
          children
        ) : (
          <>
            {loading && (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            {children}
          </>
        )}
      </Comp>
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
