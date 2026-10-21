"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/page-header";
import { ProjectForm } from "@/components/project-form";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { pick, useLocale } from "@/lib/locale";

export default function NewProjectPage() {
  const locale = useLocale();
  const router = useRouter();

  return (
    <main className="mx-auto w-full max-w-[1100px] px-6 py-10 lg:px-8 lg:py-14">
      <PageHeader
        eyebrow={
          <Button asChild variant="ghost" size="xs" className="-ml-2">
            <Link href="/">
              <ArrowLeft className="h-3.5 w-3.5" />
              {pick(locale, "返回项目", "Back to projects")}
            </Link>
          </Button>
        }
        title={pick(locale, "新建研究项目", "New research project")}
        description={pick(
          locale,
          "设定研究范围、深度、目标字数、时间范围与引用格式。启动运行后，工作流会按这些参数生成计划与提纲。",
          "Set the scope, depth, target length, date range, and citation style. The workflow uses these parameters for its plan and outline.",
        )}
      />
      <Card className="mt-8 p-6 lg:p-8">
        <ProjectForm
          onSaved={(project) => router.push(`/projects/${project.id}`)}
          onCancel={() => router.push("/")}
        />
      </Card>
    </main>
  );
}
