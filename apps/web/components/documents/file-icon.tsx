"use client";

import { FileCode, FileText, FileType } from "lucide-react";
import type { DocumentFileType } from "@/lib/types";

interface FileIconProps {
  type: DocumentFileType;
  className?: string;
}

export function FileIcon({ type, className }: FileIconProps) {
  switch (type) {
    case "pdf":
      return <FileType className={className} />;
    case "docx":
      return <FileText className={className} />;
    case "md":
      return <FileCode className={className} />;
    case "txt":
    default:
      return <FileText className={className} />;
  }
}
