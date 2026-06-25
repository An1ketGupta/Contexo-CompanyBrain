"use client";

import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FieldSchema } from "@/lib/autoflow/types";

interface FilterBuilderProps {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  fields: FieldSchema[];
}

interface FilterRow {
  key: string;
  value: string;
}

export function FilterBuilder({ value, onChange, fields }: FilterBuilderProps) {
  const rows: FilterRow[] = Object.entries(value).map(([k, v]) => ({
    key: k,
    value: Array.isArray(v) ? v.join(",") : String(v ?? ""),
  }));

  const setRows = (next: FilterRow[]) => {
    const obj: Record<string, unknown> = {};
    for (const r of next) {
      if (!r.key) continue;
      obj[r.key] = r.value.includes(",")
        ? r.value.split(",").map((s) => s.trim()).filter(Boolean)
        : r.value;
    }
    onChange(obj);
  };

  const addRow = () => setRows([...rows, { key: "", value: "" }]);
  const removeRow = (i: number) => setRows(rows.filter((_, idx) => idx !== i));
  const updateRow = (i: number, patch: Partial<FilterRow>) => {
    const next = [...rows];
    next[i] = { ...next[i], ...patch };
    setRows(next);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-xs font-medium">Payload filters</Label>
        <Button size="sm" variant="ghost" type="button" onClick={addRow} className="h-7 gap-1 text-[11px]">
          <Plus className="size-3" /> Add filter
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="rounded border border-dashed p-3 text-[11px] text-muted-foreground">
          No filters — this flow fires on every event of this type.
        </p>
      ) : (
        <div className="space-y-1.5">
          {rows.map((row, i) => (
            <div key={i} className="flex items-center gap-2">
              {fields.length > 0 ? (
                <Select
                  value={row.key}
                  onValueChange={(v) => updateRow(i, { key: v })}
                >
                  <SelectTrigger className="flex-1">
                    <SelectValue placeholder="Field" />
                  </SelectTrigger>
                  <SelectContent>
                    {fields.map((f) => (
                      <SelectItem key={f.path} value={f.path}>
                        {f.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  className="flex-1"
                  placeholder="field"
                  value={row.key}
                  onChange={(e) => updateRow(i, { key: e.target.value })}
                />
              )}
              <span className="text-xs text-muted-foreground">=</span>
              <Input
                className="flex-1"
                placeholder="value or csv,list"
                value={row.value}
                onChange={(e) => updateRow(i, { value: e.target.value })}
              />
              <Button
                size="sm"
                variant="ghost"
                type="button"
                onClick={() => removeRow(i)}
                aria-label="Remove"
                className="h-8 w-8 p-0"
              >
                <X className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        Multiple comma-separated values become an OR match (e.g. <code>tags = pricing,sales</code>).
      </p>
    </div>
  );
}
