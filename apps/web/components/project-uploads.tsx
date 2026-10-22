"use client";

import { Download, FileText, RefreshCw, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Notice } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError, apiFetch, apiUrl } from "@/lib/api";
import { formatBytes, formatDateTime } from "@/lib/format";
import { parserStatusLabel, parserStatusVariant } from "@/lib/labels";
import { pick, useLocale } from "@/lib/locale";
import type { FileListResponse, UploadedFile } from "@/lib/types";

const ACCEPTED_TYPES =
  ".pdf,.html,.htm,.md,.markdown,.txt,application/pdf,text/html,application/xhtml+xml,text/markdown,text/x-markdown,text/plain";
const PENDING_STATUSES = new Set(["pending", "parsing"]);

export function ProjectUploads({ projectId }: { projectId: string }) {
  const locale = useLocale();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<UploadedFile[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [loadingError, setLoadingError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await apiFetch<FileListResponse>(
        `/projects/${projectId}/files`,
      );
      setFiles(data.items);
      setLoadingError(null);
    } catch (caught) {
      setLoadingError(
        caught instanceof ApiError ? caught.detail : String(caught),
      );
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Parsing happens in the worker; refresh quietly while anything is pending.
  useEffect(() => {
    if (!files?.some((file) => PENDING_STATUSES.has(file.parser_status))) {
      return;
    }
    const timer = window.setTimeout(() => {
      void apiFetch<FileListResponse>(`/projects/${projectId}/files`)
        .then((data) => setFiles(data.items))
        .catch(() => {
          // Keep the current rows; the next poll retries.
        });
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [files, projectId]);

  async function upload(file: File) {
    setUploading(true);
    setUploadError(null);
    setNotice(null);
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const uploaded = await apiFetch<UploadedFile>(
        `/projects/${projectId}/files`,
        { method: "POST", body: form },
      );
      setFiles((current) => [
        uploaded,
        ...(current ?? []).filter(
          (item) => item.file_id !== uploaded.file_id,
        ),
      ]);
      setLoadingError(null);
      setNotice(
        pick(
          locale,
          `已上传“${uploaded.original_name}”，正在解析。`,
          `Uploaded “${uploaded.original_name}”; parsing started.`,
        ),
      );
    } catch (caught) {
      setUploadError(
        caught instanceof ApiError ? caught.detail : String(caught),
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <Card className="mt-6 overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-border p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold tracking-tight">
            {pick(locale, "项目资料", "Project files")}
          </h2>
          <p className="mt-0.5 text-xs leading-relaxed text-foreground-subtle">
            {pick(
              locale,
              "支持 PDF、HTML、Markdown 和纯文本。解析完成后，资料会在下一次研究运行时进入来源池。",
              "PDF, HTML, Markdown, and plain text are supported. Parsed files join the source pool on the next research run.",
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load()}
            disabled={uploading}
            aria-label={pick(locale, "刷新资料列表", "Refresh file list")}
          >
            <RefreshCw className="h-4 w-4" />
            {pick(locale, "刷新", "Refresh")}
          </Button>
          <Button
            size="sm"
            onClick={() => inputRef.current?.click()}
            loading={uploading}
          >
            <Upload className="h-4 w-4" />
            {pick(locale, "上传资料", "Upload files")}
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void upload(file);
            }}
          />
        </div>
      </div>

      {notice ? (
        <Notice variant="success" className="m-4 mb-0">
          {notice}
        </Notice>
      ) : null}
      {uploadError ? (
        <Notice
          variant="danger"
          title={pick(locale, "上传失败", "Upload failed")}
          className="m-4 mb-0"
        >
          {uploadError}
        </Notice>
      ) : null}

      {loadingError && files !== null ? (
        <Notice variant="danger" className="m-4 mb-0">
          {loadingError}
        </Notice>
      ) : null}

      {files === null ? (
        loadingError ? (
          <div className="px-5 py-6">
            <p className="text-sm text-red-fg">{loadingError}</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => void load()}
            >
              {pick(locale, "重试", "Retry")}
            </Button>
          </div>
        ) : (
          <p className="px-5 py-6 text-sm text-foreground-muted">
            {pick(locale, "加载项目资料…", "Loading project files…")}
          </p>
        )
      ) : files.length === 0 ? (
        <p className="px-5 py-8 text-sm text-foreground-subtle">
          {pick(
            locale,
            "还没有上传资料。上传的文档会在这里显示解析状态。",
            "No files uploaded yet. Uploaded documents and their parse status appear here.",
          )}
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {files.map((file) => (
            <li
              key={file.file_id}
              className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-foreground-faint" />
                  <span className="truncate text-sm font-medium">
                    {file.original_name}
                  </span>
                  <Badge
                    variant={parserStatusVariant(file.parser_status)}
                    size="xs"
                  >
                    {parserStatusLabel(file.parser_status, locale)}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-foreground-subtle">
                  {formatBytes(file.size_bytes)} · {file.mime_type} ·{" "}
                  {formatDateTime(file.created_at, locale)}
                </p>
                {file.parse_error ? (
                  <p className="mt-1 break-words text-xs text-red-fg">
                    {file.parse_error}
                  </p>
                ) : null}
              </div>
              <Button
                asChild
                variant="ghost"
                size="sm"
                className="self-start sm:self-auto"
              >
                <a href={apiUrl(`/files/${file.file_id}/download`)}>
                  <Download className="h-3.5 w-3.5" />
                  {pick(locale, "下载", "Download")}
                </a>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
