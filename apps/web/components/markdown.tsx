"use client";

import type * as React from "react";

import { cn } from "@/lib/utils";

type MarkdownProps = {
  source: string;
  citationMap?: Record<string, string>;
  onCitation?: (marker: string, sourceId: string | undefined) => void;
  className?: string;
};

function safeHref(value: string): string | null {
  const trimmed = value.trim();
  if (/^(https?:\/\/|mailto:)/i.test(trimmed)) return trimmed;
  return null;
}

function renderInline(
  text: string,
  options: MarkdownProps,
  keyPrefix: string,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern =
    /(\[S\d+\])|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(`[^`\n]+`)|(\[[^\]]+\]\([^)\s]+\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-i${index}`;
    index += 1;

    if (match[1]) {
      const marker = token.slice(1, -1);
      const sourceId = options.citationMap?.[marker];
      const known = Boolean(sourceId);
      nodes.push(
        <button
          key={key}
          type="button"
          onClick={() => options.onCitation?.(marker, sourceId)}
          disabled={!options.onCitation}
          className={cn(
            "mx-0.5 inline-flex items-center rounded-sm px-1 py-px align-baseline font-mono text-[0.72em] font-medium transition-colors",
            known
              ? "bg-blue-soft text-blue-fg hover:bg-blue-border/40"
              : "bg-red-soft text-red-fg",
            !options.onCitation && "cursor-default",
          )}
          title={known ? undefined : "Citation source is not mapped"}
        >
          {marker}
        </button>,
      );
    } else if (match[2]) {
      nodes.push(
        <strong key={key} className="font-semibold">
          {renderInline(token.slice(2, -2), options, key)}
        </strong>,
      );
    } else if (match[3]) {
      nodes.push(
        <em key={key}>{renderInline(token.slice(1, -1), options, key)}</em>,
      );
    } else if (match[4]) {
      nodes.push(
        <code
          key={key}
          className="rounded-sm bg-hover-bg-strong px-1 py-px font-mono text-[0.85em]"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (match[5]) {
      const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token);
      if (linkMatch) {
        const href = safeHref(linkMatch[2]);
        nodes.push(
          href ? (
            <a
              key={key}
              href={href}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2 hover:text-foreground-muted"
            >
              {linkMatch[1]}
            </a>
          ) : (
            <span key={key}>{linkMatch[1]}</span>
          ),
        );
      }
    }
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

export function Markdown({
  source,
  citationMap,
  onCitation,
  className,
}: MarkdownProps) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  const options: MarkdownProps = { source, citationMap, onCitation };
  let index = 0;
  let key = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(
        <pre
          key={`block-${key}`}
          className="overflow-x-auto rounded-md border border-border bg-background p-3 text-xs leading-relaxed"
        >
          <code className="font-mono">
            {language ? `${language}\n` : ""}
            {code.join("\n")}
          </code>
        </pre>,
      );
      key += 1;
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (heading) {
      const level = heading[1].length;
      const content = renderInline(heading[2], options, `h-${key}`);
      const classNames: Record<number, string> = {
        1: "mt-2 font-display text-2xl font-semibold tracking-tight",
        2: "mt-5 font-display text-xl font-semibold tracking-tight",
        3: "mt-4 font-display text-lg font-semibold tracking-tight",
        4: "mt-3 text-base font-semibold",
        5: "mt-3 text-sm font-semibold",
        6: "mt-3 text-xs font-semibold uppercase tracking-wide text-foreground-muted",
      };
      const Tag = `h${Math.min(level, 6)}` as React.ElementType;
      blocks.push(
        <Tag key={`block-${key}`} className={classNames[level]}>
          {content}
        </Tag>,
      );
      key += 1;
      index += 1;
      continue;
    }

    if (/^([-*_])\1{2,}$/.test(trimmed)) {
      blocks.push(<hr key={`block-${key}`} className="my-5 border-border" />);
      key += 1;
      index += 1;
      continue;
    }

    if (trimmed.startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quote.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(
        <blockquote
          key={`block-${key}`}
          className="border-l-2 border-border-strong pl-3 text-sm italic text-foreground-muted"
        >
          {renderInline(quote.join(" "), options, `q-${key}`)}
        </blockquote>,
      );
      key += 1;
      continue;
    }

    if (/^(\d+[.)]|[-*+])\s+/.test(trimmed)) {
      const ordered = /^\d/.test(trimmed);
      const items: string[] = [];
      while (
        index < lines.length &&
        /^(\d+[.)]|[-*+])\s+/.test(lines[index].trim())
      ) {
        items.push(lines[index].trim().replace(/^(\d+[.)]|[-*+])\s+/, ""));
        index += 1;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag
          key={`block-${key}`}
          className={cn(
            "space-y-1 pl-5 text-sm leading-relaxed",
            ordered ? "list-decimal" : "list-disc",
          )}
        >
          {items.map((item, itemIndex) => (
            <li key={`${key}-${itemIndex}`}>
              {renderInline(item, options, `li-${key}-${itemIndex}`)}
            </li>
          ))}
        </ListTag>,
      );
      key += 1;
      continue;
    }

    if (
      trimmed.includes("|") &&
      index + 1 < lines.length &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1]) &&
      /[-:]/.test(lines[index + 1])
    ) {
      const headers = splitRow(lines[index]);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].includes("|")) {
        rows.push(splitRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <div key={`block-${key}`} className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-border text-left">
                {headers.map((header, headerIndex) => (
                  <th
                    key={`${key}-h-${headerIndex}`}
                    className="px-2 py-1.5 font-medium text-foreground-muted"
                  >
                    {renderInline(header, options, `th-${key}-${headerIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${key}-r-${rowIndex}`} className="border-b border-border last:border-0">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={`${key}-c-${rowIndex}-${cellIndex}`}
                      className="px-2 py-1.5 align-top"
                    >
                      {renderInline(cell, options, `td-${key}-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      key += 1;
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index].trim();
      if (!current || /^(#{1,6}\s|>|```|(\d+[.)]|[-*+])\s)/.test(current)) {
        break;
      }
      paragraph.push(current);
      index += 1;
    }
    blocks.push(
      <p key={`block-${key}`} className="text-sm leading-relaxed">
        {renderInline(paragraph.join(" "), options, `p-${key}`)}
      </p>,
    );
    key += 1;
  }

  return (
    <div className={cn("flex flex-col gap-3 text-foreground", className)}>
      {blocks}
    </div>
  );
}
