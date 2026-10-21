"use client";

import type { ReactNode } from "react";

import { RunWorkspaceLayout } from "@/components/run/run-workspace";

export default function RunLayout({ children }: { children: ReactNode }) {
  return <RunWorkspaceLayout>{children}</RunWorkspaceLayout>;
}
