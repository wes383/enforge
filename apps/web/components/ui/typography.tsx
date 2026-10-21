import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const headingVariants = cva("font-display tracking-tight text-foreground", {
  variants: {
    level: {
      h1: "text-5xl font-bold",
      h2: "text-4xl font-semibold",
      h3: "text-3xl font-semibold",
      h4: "text-2xl font-semibold",
      h5: "text-xl font-semibold",
      h6: "text-lg font-semibold",
    },
  },
  defaultVariants: { level: "h2" },
});

export type HeadingLevel = "h1" | "h2" | "h3" | "h4" | "h5" | "h6";

export interface HeadingProps
  extends React.HTMLAttributes<HTMLHeadingElement>,
    VariantProps<typeof headingVariants> {
  as?: HeadingLevel;
}

export const Heading = React.forwardRef<HTMLHeadingElement, HeadingProps>(
  ({ className, level, as, ...props }, ref) => {
    const Comp = (as ?? level ?? "h2") as React.ElementType;
    return (
      <Comp
        ref={ref}
        className={cn(headingVariants({ level, className }))}
        {...props}
      />
    );
  },
);
Heading.displayName = "Heading";

const textVariants = cva("text-foreground", {
  variants: {
    variant: {
      default: "text-base text-foreground",
      muted: "text-base text-foreground-muted",
      subtle: "text-sm text-foreground-subtle",
      faint: "text-xs text-foreground-faint",
      small: "text-sm text-foreground",
      lead: "text-lg text-foreground-muted",
      large: "text-lg text-foreground",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface TextProps
  extends React.HTMLAttributes<HTMLElement>,
    VariantProps<typeof textVariants> {
  as?: React.ElementType;
}

export const Text = React.forwardRef<HTMLElement, TextProps>(
  ({ className, variant, as: Comp = "p", ...props }, ref) => {
    const Element = Comp as React.ElementType;
    return (
      <Element ref={ref} className={cn(textVariants({ variant, className }))} {...props} />
    );
  },
);
Text.displayName = "Text";

export const Code = React.forwardRef<
  HTMLElement,
  React.HTMLAttributes<HTMLElement>
>(({ className, ...props }, ref) => (
  <code
    ref={ref}
    className={cn(
      "font-mono text-sm bg-hover-bg-strong text-foreground px-1.5 py-0.5 rounded-sm",
      className,
    )}
    {...props}
  />
));
Code.displayName = "Code";
