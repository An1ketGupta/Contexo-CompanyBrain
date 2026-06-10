"use client";

import {
  FileCode,
  FileSpreadsheet,
  FileText,
  FileType,
  Presentation,
} from "lucide-react";
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
    case "html":
      return <FileCode className={className} />;
    case "xlsx":
    case "csv":
      return <FileSpreadsheet className={className} />;
    case "pptx":
      return <Presentation className={className} />;
    case "txt":
    default:
      return <FileText className={className} />;
  }
}
