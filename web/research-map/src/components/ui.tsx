// Small component kit in the shadcn/ui manner: Radix primitives for behaviour and
// accessibility, Tailwind for the look, class-variance-authority for variants.
import * as RadixScroll from "@radix-ui/react-scroll-area";
import * as RadixToggle from "@radix-ui/react-toggle-group";
import * as RadixTooltip from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const button = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors " +
    "disabled:pointer-events-none disabled:opacity-40 select-none",
  {
    variants: {
      variant: {
        ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
        outline: "border border-line bg-surface text-ink hover:border-line-strong hover:bg-surface-2",
        solid: "bg-ink text-bg hover:opacity-90",
      },
      size: {
        sm: "h-8 px-2.5 text-[0.8125rem]",
        md: "h-9 px-3 text-sm",
        icon: "h-8 w-8 text-sm",
      },
    },
    defaultVariants: { variant: "ghost", size: "md" },
  },
);

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button>
>(function Button({ className, variant, size, ...props }, ref) {
  return <button ref={ref} className={cn(button({ variant, size }), className)} {...props} />;
});

export const TooltipProvider = RadixTooltip.Provider;

export function Tip({ label, children, side = "bottom" }: { label: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <RadixTooltip.Root delayDuration={250}>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={6}
          className="z-50 max-w-72 rounded-md bg-ink px-2.5 py-1.5 text-[0.75rem] leading-snug text-bg shadow-card"
        >
          {label}
          <RadixTooltip.Arrow className="fill-ink" />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  items,
  label,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: ReactNode; count?: number; tone?: "alarm" }[];
  label: string;
  className?: string;
}) {
  return (
    <RadixToggle.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      aria-label={label}
      className={cn("inline-flex h-9 items-center rounded-[0.6rem] border border-line bg-surface-2 p-0.5", className)}
    >
      {items.map((it) => (
        <RadixToggle.Item
          key={it.value}
          value={it.value}
          className={cn(
            "inline-flex h-full items-center gap-1.5 rounded-[0.45rem] px-3 text-[0.8125rem] font-medium text-ink-2 transition-colors",
            "hover:text-ink data-[state=on]:bg-surface data-[state=on]:text-ink data-[state=on]:shadow-card",
          )}
        >
          {it.label}
          {it.count !== undefined && it.count > 0 && (
            <span
              className={cn(
                "min-w-[1.25rem] rounded-full px-1.5 text-center font-mono text-[0.6875rem] leading-[1.25rem]",
                it.tone === "alarm" ? "bg-redpen text-white" : "bg-line text-ink-2",
              )}
            >
              {it.count}
            </span>
          )}
        </RadixToggle.Item>
      ))}
    </RadixToggle.Root>
  );
}

export function Scroll({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <RadixScroll.Root className={cn("overflow-hidden", className)} type="hover">
      <RadixScroll.Viewport className="h-full w-full [&>div]:!block">{children}</RadixScroll.Viewport>
      <RadixScroll.Scrollbar orientation="vertical" className="flex w-2 touch-none select-none p-0.5">
        <RadixScroll.Thumb className="relative flex-1 rounded-full bg-line-strong" />
      </RadixScroll.Scrollbar>
    </RadixScroll.Root>
  );
}

export function Pill({ children, className, dashed }: { children: ReactNode; className?: string; dashed?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-[1.375rem] items-center gap-1 rounded-full border px-2 text-[0.6875rem] font-medium leading-none",
        dashed ? "border-dashed border-line-strong text-ink-2" : "border-line text-ink-2",
        className,
      )}
    >
      {children}
    </span>
  );
}
