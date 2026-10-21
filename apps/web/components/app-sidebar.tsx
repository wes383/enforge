"use client";

import {
  ArrowUpRight,
  FolderOpen,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Sun,
  X,
} from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { API_BASE_URL, apiFetch } from "@/lib/api";
import { persistLocale, pick, useLocale } from "@/lib/locale";
import type { Locale, Project, ProjectListResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const LOCALES: { value: Locale; label: string }[] = [
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
];

const COLLAPSE_STORAGE_KEY = "enforge.sidebar-collapsed";

export function AppSidebar() {
  const locale = useLocale();
  const pathname = usePathname();
  const { setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectsError, setProjectsError] = useState(false);
  const mounted = useRef(false);

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

  // next-themes reports the resolved theme asynchronously, so the active switch
  // follows the class the provider actually applied to <html>.
  const [appliedTheme, setAppliedTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    const root = document.documentElement;
    const sync = () => setAppliedTheme(root.classList.contains("dark") ? "dark" : "light");
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(COLLAPSE_STORAGE_KEY) === "true");
    } catch {
      // Private browsing keeps the sidebar expanded.
    }
  }, []);

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    try {
      window.localStorage.setItem(COLLAPSE_STORAGE_KEY, String(collapsed));
    } catch {
      // Ignore storage failures; the state still applies to this session.
    }
  }, [collapsed]);

  // The content column offsets itself from this token so collapsing reflows the
  // page without lifting sidebar state into the root layout.
  useEffect(() => {
    document.documentElement.dataset.sidebar = collapsed ? "collapsed" : "expanded";
  }, [collapsed]);

  const loadProjects = useCallback(async () => {
    try {
      const response = await apiFetch<ProjectListResponse>("/projects?limit=50");
      setProjects(response.items);
      setProjectsError(false);
    } catch {
      setProjectsError(true);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects, pathname]);

  const sectionLabel =
    "text-[11px] font-medium uppercase tracking-wide text-foreground-subtle";

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
        data-collapsed={collapsed ? "true" : "false"}
        className={cn(
          "fixed inset-y-0 left-0 z-sticky flex w-[var(--sidebar-width)] flex-col border-r border-border bg-sidebar transition-[width,transform] duration-base ease-out lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div
          className={cn(
            "flex items-center gap-2 px-3 py-4",
            collapsed ? "lg:justify-center lg:px-2" : "justify-between",
          )}
        >
          <Link
            href="/"
            className={cn(
              "min-w-0 font-display text-base font-bold leading-none tracking-tight transition-colors hover:text-foreground-muted",
              collapsed && "lg:hidden",
            )}
          >
            Enforge
          </Link>
          <span
            aria-hidden
            className={cn(
              "hidden h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent text-xs font-bold text-accent-fg lg:inline-flex",
              !collapsed && "lg:hidden",
            )}
          >
            E
          </span>
          <Button
            variant="ghost"
            size="icon-xs"
            className="-mr-1 lg:hidden"
            onClick={() => setOpen(false)}
            aria-label={pick(locale, "收起导航", "Close navigation")}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div
          className={cn(
            "flex items-center justify-between gap-2 px-3 pb-1",
            collapsed && "lg:justify-center lg:px-2",
          )}
        >
          <p className={cn(sectionLabel, collapsed && "lg:hidden")}>
            {pick(locale, "项目", "Projects")}
          </p>
          <Link
            href="/projects/new"
            aria-label={pick(locale, "新建项目", "New project")}
            title={pick(locale, "新建项目", "New project")}
            className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-foreground-muted transition-colors hover:bg-hover-bg hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
          </Link>
        </div>

        <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-3 pb-2">
          <Link
            href="/"
            aria-current={pathname === "/" ? "page" : undefined}
            className={cn(
              "inline-flex shrink-0 items-center gap-2.5 rounded-full px-2.5 py-2 text-sm transition-colors",
              collapsed && "lg:justify-center lg:px-0",
              pathname === "/"
                ? "bg-hover-bg font-medium text-foreground"
                : "text-foreground-muted hover:bg-hover-bg hover:text-foreground",
            )}
          >
            <FolderOpen className="h-4 w-4 shrink-0" />
            <span className={cn("truncate", collapsed && "lg:hidden")}>
              {pick(locale, "全部项目", "All projects")}
            </span>
          </Link>

          {projectsError ? (
            <button
              type="button"
              onClick={() => void loadProjects()}
              className={cn(
                "rounded-full px-2.5 py-2 text-left text-xs text-red-fg transition-colors hover:bg-hover-bg",
                collapsed && "lg:hidden",
              )}
            >
              {pick(locale, "项目加载失败，点击重试", "Could not load projects, retry")}
            </button>
          ) : projects === null ? (
            <div className={cn("space-y-1.5 px-2.5 py-2", collapsed && "lg:hidden")}>
              {[0, 1, 2].map((index) => (
                <div
                  key={index}
                  className="h-3.5 animate-pulse rounded-full bg-hover-bg"
                />
              ))}
            </div>
          ) : projects.length === 0 ? (
            <p className={cn("px-2.5 py-2 text-xs text-foreground-subtle", collapsed && "lg:hidden")}>
              {pick(locale, "还没有项目", "No projects yet")}
            </p>
          ) : (
            projects.map((project) => {
              const href = `/projects/${project.id}`;
              const active = pathname === href || pathname.startsWith(`${href}/`);
              return (
                <Link
                  key={project.id}
                  href={href}
                  title={project.title}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group inline-flex min-w-0 items-center gap-2 rounded-full px-2.5 py-2 text-sm transition-colors",
                    collapsed && "lg:hidden",
                    active
                      ? "bg-hover-bg font-medium text-foreground"
                      : "text-foreground-muted hover:bg-hover-bg hover:text-foreground",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      active ? "bg-accent" : "bg-foreground-faint",
                    )}
                  />
                  <span className="truncate">{project.title}</span>
                </Link>
              );
            })
          )}
        </nav>

        <div
          className={cn(
            "flex flex-col gap-3 border-t border-border px-3 py-4",
            collapsed && "lg:items-center lg:px-2",
          )}
        >
          <div className={cn(collapsed && "lg:hidden")}>
            <p className={cn(sectionLabel, "mb-1.5")}>
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
            <p className={cn(sectionLabel, "mb-1.5", collapsed && "lg:hidden")}>
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
                  aria-pressed={appliedTheme === option.value}
                  title={option.label}
                  onClick={() => setTheme(option.value)}
                  className={cn(
                    "inline-flex flex-1 items-center justify-center gap-1.5 rounded-full px-2 py-1.5 text-xs font-medium transition-colors",
                    collapsed && "lg:gap-0 lg:px-1.5",
                    appliedTheme === option.value
                      ? "bg-accent text-accent-fg"
                      : "text-foreground-muted hover:text-foreground",
                  )}
                >
                  <option.Icon className="h-3.5 w-3.5 shrink-0" />
                  <span className={cn(collapsed && "lg:hidden")}>{option.label}</span>
                </button>
              ))}
            </div>
          </div>

          <div className={cn("flex items-center justify-between gap-2", collapsed && "lg:flex-col")}>
            <a
              href={`${API_BASE_URL}/docs`}
              target="_blank"
              rel="noreferrer"
              title={pick(locale, "API 文档", "API docs")}
              className={cn(
                "inline-flex items-center gap-1.5 text-xs text-foreground-muted transition-colors hover:text-foreground",
                collapsed && "lg:justify-center",
              )}
            >
              <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
              <span className={cn(collapsed && "lg:hidden")}>
                {pick(locale, "API 文档", "API docs")}
              </span>
            </a>

            <button
              type="button"
              onClick={() => setCollapsed((value) => !value)}
              aria-pressed={collapsed}
              title={
                collapsed
                  ? pick(locale, "展开侧边栏", "Expand sidebar")
                  : pick(locale, "收起侧边栏", "Collapse sidebar")
              }
              className="hidden h-7 w-7 items-center justify-center rounded-full text-foreground-muted transition-colors hover:bg-hover-bg hover:text-foreground lg:inline-flex"
            >
              {collapsed ? (
                <PanelLeftOpen className="h-4 w-4" />
              ) : (
                <PanelLeftClose className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
