"use client";

import { useState, type FormEvent } from "react";

import { Notice } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, apiFetch } from "@/lib/api";
import { ARTICLE_TYPES } from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type {
  ArticleType,
  CitationStyle,
  Project,
  ProjectLanguage,
  ResearchDepth,
} from "@/lib/types";

type Draft = {
  title: string;
  topic: string;
  research_scope: string;
  article_type: ArticleType;
  target_audience: string;
  language: ProjectLanguage;
  research_depth: ResearchDepth;
  citation_style: CitationStyle;
  target_words: string;
  date_from: string;
  date_to: string;
  budget_usd: string;
};

function draftFrom(project?: Project): Draft {
  return {
    title: project?.title ?? "",
    topic: project?.topic ?? "",
    research_scope: project?.research_scope ?? "",
    article_type: project?.article_type ?? "investigative_research",
    target_audience: project?.target_audience ?? "",
    language: project?.language ?? "zh",
    research_depth: project?.research_depth ?? "standard",
    citation_style: project?.citation_style ?? "apa",
    target_words:
      project?.target_words !== null && project?.target_words !== undefined
        ? String(project.target_words)
        : "",
    date_from: project?.date_from ?? "",
    date_to: project?.date_to ?? "",
    budget_usd:
      project?.budget_usd !== null && project?.budget_usd !== undefined
        ? String(project.budget_usd)
        : "",
  };
}

export function ProjectForm({
  project,
  onSaved,
  onCancel,
}: {
  project?: Project;
  onSaved: (project: Project) => void;
  onCancel?: () => void;
}) {
  const locale = useLocale();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(project));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [requestError, setRequestError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function validate(): Record<string, string> {
    const next: Record<string, string> = {};
    if (!draft.title.trim()) {
      next.title = pick(locale, "请输入项目标题", "Project title is required");
    }
    if (!draft.topic.trim()) {
      next.topic = pick(locale, "请输入研究主题", "Research topic is required");
    }
    if (draft.target_words.trim()) {
      const value = Number(draft.target_words);
      if (!Number.isInteger(value) || value < 100 || value > 50000) {
        next.target_words = pick(
          locale,
          "目标字数需为 100–50000 的整数",
          "Target words must be an integer from 100 to 50000",
        );
      }
    }
    if (draft.date_from && draft.date_to && draft.date_to < draft.date_from) {
      next.date_to = pick(
        locale,
        "结束日期不能早于开始日期",
        "End date must not be before the start date",
      );
    }
    if (draft.budget_usd.trim()) {
      const value = Number(draft.budget_usd);
      if (!Number.isFinite(value) || value <= 0) {
        next.budget_usd = pick(
          locale,
          "预算需为正数",
          "Budget must be greater than zero",
        );
      }
    }
    return next;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRequestError(null);
    const validation = validate();
    setErrors(validation);
    if (Object.keys(validation).length > 0) return;

    const payload = {
      title: draft.title.trim(),
      topic: draft.topic.trim(),
      research_scope: draft.research_scope.trim() || null,
      article_type: draft.article_type,
      target_audience: draft.target_audience.trim() || null,
      language: draft.language,
      research_depth: draft.research_depth,
      citation_style: draft.citation_style,
      target_words: draft.target_words.trim()
        ? Number(draft.target_words)
        : null,
      date_from: draft.date_from || null,
      date_to: draft.date_to || null,
      budget_usd: draft.budget_usd.trim() ? Number(draft.budget_usd) : null,
    };

    setSaving(true);
    try {
      const saved = project
        ? await apiFetch<Project>(`/projects/${project.id}`, {
            method: "PATCH",
            body: JSON.stringify(payload),
          })
        : await apiFetch<Project>("/projects", {
            method: "POST",
            body: JSON.stringify(payload),
          });
      onSaved(saved);
    } catch (error) {
      setRequestError(
        error instanceof ApiError ? error.detail : String(error),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="flex flex-col gap-8" onSubmit={handleSubmit} noValidate>
      {requestError ? (
        <Notice variant="danger" title={pick(locale, "保存失败", "Could not save")}>
          {requestError}
        </Notice>
      ) : null}

      <section className="grid gap-5 lg:grid-cols-2">
        <Field
          label={pick(locale, "项目标题", "Project title")}
          htmlFor="project-title"
          error={errors.title}
          className="lg:col-span-2"
        >
          <Input
            id="project-title"
            value={draft.title}
            onChange={(event) => update("title", event.target.value)}
            placeholder={pick(
              locale,
              "例如：中国新能源汽车产业政策研究",
              "e.g. Industrial policy for electric vehicles",
            )}
            maxLength={300}
          />
        </Field>

        <Field
          label={pick(locale, "研究主题", "Research topic")}
          htmlFor="project-topic"
          error={errors.topic}
          className="lg:col-span-2"
        >
          <Textarea
            id="project-topic"
            value={draft.topic}
            onChange={(event) => update("topic", event.target.value)}
            placeholder={pick(
              locale,
              "描述研究主题、范围与背景",
              "Describe the research topic, scope, and background",
            )}
            rows={3}
            maxLength={20000}
          />
        </Field>

        <Field
          label={pick(locale, "文章类型", "Article type")}
          htmlFor="project-article-type"
          hint={
            ARTICLE_TYPES[draft.article_type]
              ? locale === "zh"
                ? ARTICLE_TYPES[draft.article_type].description.zh
                : ARTICLE_TYPES[draft.article_type].description.en
              : undefined
          }
          className="lg:col-span-2"
        >
          <Select
            id="project-article-type"
            value={draft.article_type}
            onChange={(event) =>
              update("article_type", event.target.value as ArticleType)
            }
          >
            {Object.entries(ARTICLE_TYPES).map(([value, meta]) => (
              <option key={value} value={value}>
                {locale === "zh" ? meta.label.zh : meta.label.en}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label={pick(locale, "研究范围", "Research scope")}
          htmlFor="project-scope"
          hint={pick(
            locale,
            "限定地域、行业、方法或必须涵盖的内容",
            "Limit geography, industry, methods, or required coverage",
          )}
          className="lg:col-span-2"
        >
          <Textarea
            id="project-scope"
            value={draft.research_scope}
            onChange={(event) => update("research_scope", event.target.value)}
            rows={2}
            maxLength={20000}
          />
        </Field>
      </section>

      <section className="border-t border-border pt-6">
        <h2 className="font-display text-base font-semibold tracking-tight">
          {pick(locale, "研究参数", "Research parameters")}
        </h2>
        <div className="mt-4 grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
          <Field label={pick(locale, "语言", "Language")} htmlFor="project-language">
            <Select
              id="project-language"
              value={draft.language}
              onChange={(event) =>
                update("language", event.target.value as ProjectLanguage)
              }
            >
              <option value="zh">中文</option>
              <option value="en">English</option>
            </Select>
          </Field>
          <Field label={pick(locale, "研究深度", "Research depth")} htmlFor="project-depth">
            <Select
              id="project-depth"
              value={draft.research_depth}
              onChange={(event) =>
                update("research_depth", event.target.value as ResearchDepth)
              }
            >
              <option value="quick">{pick(locale, "快速", "Quick")}</option>
              <option value="standard">
                {pick(locale, "标准", "Standard")}
              </option>
              <option value="deep">{pick(locale, "深入", "Deep")}</option>
            </Select>
          </Field>
          <Field
            label={pick(locale, "目标字数", "Target words")}
            htmlFor="project-words"
            error={errors.target_words}
            hint={pick(locale, "100–50000，留空自动决定", "100–50000; auto if empty")}
          >
            <Input
              id="project-words"
              type="number"
              min={100}
              max={50000}
              step={100}
              value={draft.target_words}
              onChange={(event) => update("target_words", event.target.value)}
            />
          </Field>
          <Field label={pick(locale, "引用格式", "Citation style")} htmlFor="project-citation">
            <Select
              id="project-citation"
              value={draft.citation_style}
              onChange={(event) =>
                update("citation_style", event.target.value as CitationStyle)
              }
            >
              <option value="apa">APA</option>
              <option value="ieee">IEEE</option>
              <option value="chicago">Chicago</option>
              <option value="mla">MLA</option>
            </Select>
          </Field>
          <Field label={pick(locale, "起始日期", "From date")} htmlFor="project-from">
            <Input
              id="project-from"
              type="date"
              value={draft.date_from}
              onChange={(event) => update("date_from", event.target.value)}
            />
          </Field>
          <Field
            label={pick(locale, "结束日期", "To date")}
            htmlFor="project-to"
            error={errors.date_to}
          >
            <Input
              id="project-to"
              type="date"
              value={draft.date_to}
              onChange={(event) => update("date_to", event.target.value)}
            />
          </Field>
          <Field
            label={pick(locale, "目标读者", "Audience")}
            htmlFor="project-audience"
          >
            <Input
              id="project-audience"
              value={draft.target_audience}
              onChange={(event) => update("target_audience", event.target.value)}
              placeholder={pick(locale, "可选", "Optional")}
              maxLength={1000}
            />
          </Field>
          <Field
            label={pick(locale, "预算 (USD)", "Budget (USD)")}
            htmlFor="project-budget"
            error={errors.budget_usd}
          >
            <Input
              id="project-budget"
              type="number"
              min={0}
              step="0.01"
              value={draft.budget_usd}
              onChange={(event) => update("budget_usd", event.target.value)}
              placeholder={pick(locale, "可选", "Optional")}
            />
          </Field>
        </div>
      </section>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-5">
        {onCancel ? (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
            {pick(locale, "取消", "Cancel")}
          </Button>
        ) : null}
        <Button type="submit" loading={saving}>
          {project
            ? pick(locale, "保存设置", "Save settings")
            : pick(locale, "创建项目", "Create project")}
        </Button>
      </div>
    </form>
  );
}
