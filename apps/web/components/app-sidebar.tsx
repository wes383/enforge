"use client";

import {
  ArrowUpRight,
  FolderOpen,
  Menu,
  Moon,
  Plus,
  Sun,
  X,
} from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { API_BASE_URL } from "@/lib/api";
import { persistLocale, pick, useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/types";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  {
    href: "/",
    icon: FolderOpen,
    labels: ["研究项目", "Projects"] as const,
    exact: true,
  },
  {
    href: "/projects/new",
    icon: Plus,
    labels: ["新建项目", "New project"] as const,
  },
];

const LOCALES: { value: Locale; label: string }[] = [
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
];

export function AppSidebar() {
  const locale = useLocale();
  const pathname = usePathname();
  const { resolvedTheme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  }, [locale]);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <>
      <header className="flex items-center border-b border-border bg-sidebar px-3 py-2.5 lg:hidden">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOpen(true)}
          aria-label={pick(locale, "打开导航", "Open navigation")}
        >
          <Menu className="h-4 w-4" />
          {pick(locale, "导航", "Menu")}
        </Button>
      </header>

      {open ? (
        <div
          className="fixed inset-0 z-dropdown bg-overlay lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden
        />
      ) : null}

      <aside
        aria-label={pick(locale, "侧边导航", "Sidebar navigation")}
        className={cn(
          "fixed inset-y-0 left-0 z-sticky flex w-64 flex-col border-r border-border bg-sidebar transition-transform duration-base ease-out lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-start justify-between gap-2 px-5 py-5">
          <div className="min-w-0">
            <Link
              href="/"
              className="font-display text-base font-bold leading-none tracking-tight transition-colors hover:text-foreground-muted"
            >
              Enforge
            </Link>
            <p className="mt-1.5 truncate text-xs text-foreground-subtle">
              {pick(locale, "深度研究写作代理", "Deep research writing agent")}
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon-xs"
            className="-mr-1 -mt-1 lg:hidden"
            onClick={() => setOpen(false)}
            aria-label={pick(locale, "收起导航", "Close navigation")}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <nav className="flex flex-col gap-1 px-3">
          {NAV_ITEMS.map((item) => {
            const active = item.exact
              ? pathname === item.href
              : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex items-center gap-2.5 rounded-full px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-hover-bg font-medium text-foreground"
                    : "text-foreground-muted hover:bg-hover-bg hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {pick(locale, item.labels[0], item.labels[1])}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto flex flex-col gap-4 border-t border-border px-5 py-5">
          <div>
            <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
              {pick(locale, "语言", "Language")}
            </p>
            <div className="flex items-center gap-1 rounded-full bg-hover-bg p-1">
              {LOCALES.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={locale === option.value}
                  onClick={() => persistLocale(option.value)}
                  className={cn(
                    "flex-1 rounded-full px-2 py-1.5 text-xs font-medium transition-colors",
                    locale === option.value
                      ? "bg-accent text-accent-fg"
                      : "text-foreground-muted hover:text-foreground",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-foreground-subtle">
              {pick(locale, "主题", "Theme")}
            </p>
            <div className="flex items-center gap-1 rounded-full bg-hover-bg p-1">
              {(
                [
                  { value: "light", label: pick(locale, "浅色", "Light"), Icon: Sun },
                  { value: "dark", label: pick(locale, "深色", "Dark"), Icon: Moon },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={resolvedTheme === option.value}
                  onClick={() => setTheme(option.value)}
                  className={cn(
                    "inline-flex flex-1 items-center justify-center gap-1.5 rounded-full px-2 py-1.5 text-xs font-medium transition-colors",
                    resolvedTheme === option.value
                      ? "bg-accent text-accent-fg"
                      : "text-foreground-muted hover:text-foreground",
                  )}
                >
                  <option.Icon className="h-3.5 w-3.5" />
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <a
            href={`${API_BASE_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-xs text-foreground-muted transition-colors hover:text-foreground"
          >
            <ArrowUpRight className="h-3.5 w-3.5" />
            {pick(locale, "API 文档", "API docs")}
            <span className="truncate font-mono text-[10px] text-foreground-faint">
              {API_BASE_URL}
            </span>
          </a>
        </div>
      </aside>
    </>
  );
}
