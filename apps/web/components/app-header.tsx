"use client";

import { ArrowUpRight, Languages, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { API_BASE_URL } from "@/lib/api";
import { persistLocale, pick, useLocale } from "@/lib/locale";
import { cn } from "@/lib/utils";

export function AppHeader() {
  const locale = useLocale();
  const pathname = usePathname();
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  }, [locale]);

  return (
    <header className="sticky top-0 z-sticky border-b border-border bg-background/95 backdrop-blur">
      <div className="mx-auto flex min-h-16 max-w-[1400px] items-center justify-between gap-4 px-6 py-2 lg:px-8">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/"
            className="shrink-0 font-display text-base font-bold leading-none tracking-tight transition-colors hover:text-foreground-muted"
          >
            Enforge
          </Link>
          <Badge variant="secondary" size="xs" className="hidden shrink-0 sm:inline-flex">
            {pick(locale, "阶段 08", "Phase 08")}
          </Badge>
          <nav className="ml-2 hidden items-center gap-1 sm:flex">
            <Link
              href="/"
              className={cn(
                "rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                pathname === "/"
                  ? "bg-hover-bg text-foreground"
                  : "text-foreground-muted hover:bg-hover-bg hover:text-foreground",
              )}
            >
              {pick(locale, "研究项目", "Projects")}
            </Link>
          </nav>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button asChild variant="ghost" size="sm" className="px-3">
            <a
              href={`${API_BASE_URL}/docs`}
              target="_blank"
              rel="noreferrer"
              aria-label={pick(locale, "API 文档", "API docs")}
            >
              <span className="hidden sm:inline">
                {pick(locale, "API 文档", "API docs")}
              </span>
              <ArrowUpRight className="h-3.5 w-3.5" />
            </a>
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => persistLocale(locale === "zh" ? "en" : "zh")}
            aria-label={pick(locale, "切换语言", "Switch language")}
            className="gap-1.5 px-3"
          >
            <Languages className="h-4 w-4" />
            <span className="text-xs">{locale === "zh" ? "中" : "EN"}</span>
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
            aria-label={pick(locale, "切换主题", "Toggle theme")}
          >
            <Sun className="h-4 w-4 dark:hidden" />
            <Moon className="hidden h-4 w-4 dark:block" />
          </Button>
        </div>
      </div>
    </header>
  );
}
