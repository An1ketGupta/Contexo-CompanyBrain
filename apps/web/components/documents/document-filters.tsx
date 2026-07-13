"use client";

import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useDebounced } from "@/hooks/use-debounced";
import {
  DEFAULT_FILTERS,
  isFiltering,
  type DocumentFilters,
} from "@/hooks/use-documents";

interface Props {
  filters: DocumentFilters;
  onChange: (next: DocumentFilters) => void;
  totalShown: number;
  totalAvailable: number;
}

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Any status" },
  { value: "ready", label: "Ready" },
  { value: "processing", label: "Processing" },
  { value: "pending", label: "Pending" },
  { value: "failed", label: "Failed" },
];

const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Any type" },
  { value: "pdf", label: "PDF" },
  { value: "docx", label: "DOCX" },
  { value: "txt", label: "TXT" },
  { value: "md", label: "Markdown" },
];

const KIND_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All documents" },
  { value: "meeting_transcript", label: "Meeting transcripts only" },
];

const SORT_OPTIONS: {
  value: DocumentFilters["sort_by"];
  label: string;
}[] = [
  { value: "created_at", label: "Uploaded" },
  { value: "name", label: "Name" },
  { value: "file_size_bytes", label: "Size" },
];

export function DocumentFiltersBar({
  filters,
  onChange,
  totalShown,
  totalAvailable,
}: Props) {
  const [searchInput, setSearchInput] = useState(filters.search);
  const debouncedSearch = useDebounced(searchInput, 300);

  useEffect(() => {
    // Sync upstream when the debounced value differs. Comparing avoids a
    // re-render loop when parent state echoes back identical.
    if (debouncedSearch !== filters.search) {
      onChange({ ...filters, search: debouncedSearch });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  useEffect(() => {
    setSearchInput(filters.search);
  }, [filters.search]);

  const active = isFiltering(filters);
  const showFilteredCount = active || totalShown !== totalAvailable;

  return (
    <div className="mb-3 flex w-full flex-col gap-2">
      <div className="flex w-full flex-wrap items-center gap-2">
        <div className="relative min-w-64 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search documents…"
            className="h-9 pl-8 pr-7"
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => setSearchInput("")}
              className="absolute right-2 top-2.5 text-muted-foreground hover:text-foreground"
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <NativeSelect
          value={filters.status}
          onChange={(v) => onChange({ ...filters, status: v })}
          options={STATUS_OPTIONS}
          aria-label="Filter by status"
        />
        <NativeSelect
          value={filters.file_type}
          onChange={(v) => onChange({ ...filters, file_type: v })}
          options={TYPE_OPTIONS}
          aria-label="Filter by file type"
        />
        <NativeSelect
          value={filters.kind}
          onChange={(v) => onChange({ ...filters, kind: v })}
          options={KIND_OPTIONS}
          aria-label="Filter by document kind"
        />

        <div className="ml-auto flex items-center gap-1.5">
          <NativeSelect
            value={filters.sort_by}
            onChange={(v) =>
              onChange({ ...filters, sort_by: v as DocumentFilters["sort_by"] })
            }
            options={SORT_OPTIONS}
            aria-label="Sort by"
          />
          <Button
            variant="outline"
            size="icon"
            onClick={() =>
              onChange({
                ...filters,
                sort_dir: filters.sort_dir === "asc" ? "desc" : "asc",
              })
            }
            aria-label={`Sort ${filters.sort_dir === "asc" ? "descending" : "ascending"}`}
          >
            {filters.sort_dir === "asc" ? (
              <ArrowUp className="h-3.5 w-3.5" />
            ) : (
              <ArrowDown className="h-3.5 w-3.5" />
            )}
          </Button>
          {active && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onChange(DEFAULT_FILTERS)}
            >
              Clear
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>

      {showFilteredCount && (
        <div className="text-xs text-muted-foreground">
          <Badge variant="outline" className="px-1.5 py-0">
            {totalShown}
          </Badge>{" "}
          shown
          {totalAvailable !== totalShown ? ` of ${totalAvailable} matching` : null}
        </div>
      )}
    </div>
  );
}

function NativeSelect({
  value,
  onChange,
  options,
  ...rest
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
} & Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange">) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-[10px] border border-input bg-background px-2.5 text-sm shadow-sm transition-colors focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
      {...rest}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}
