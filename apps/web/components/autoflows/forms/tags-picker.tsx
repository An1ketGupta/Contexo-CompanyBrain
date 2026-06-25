"use client";

import { useState } from "react";
import useSWR from "swr";
import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

interface TagsPickerProps {
  value: string[];
  onChange: (next: string[]) => void;
  description?: string;
  label?: string;
}

const fetcher = async (url: string): Promise<{ tags: Array<{ name: string }> }> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export function TagsPicker({ value, onChange, description, label = "Tags" }: TagsPickerProps) {
  const [input, setInput] = useState("");
  const { data } = useSWR<{ tags: Array<{ name: string }> }>("/api/documents/tags", fetcher, {
    revalidateOnFocus: false,
  });
  const suggestions = (data?.tags ?? [])
    .map((t) => t.name)
    .filter((n) => !value.includes(n))
    .filter((n) => !input || n.toLowerCase().includes(input.toLowerCase()))
    .slice(0, 8);

  const add = (tag: string) => {
    const clean = tag.trim();
    if (!clean || value.includes(clean)) return;
    onChange([...value, clean]);
    setInput("");
  };

  const remove = (tag: string) => onChange(value.filter((t) => t !== tag));

  return (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium">{label}</Label>
      <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-input bg-background p-1.5">
        {value.map((t) => (
          <Badge key={t} variant="accent" className="gap-1 text-[11px]">
            {t}
            <button
              type="button"
              onClick={() => remove(t)}
              className="ml-0.5 -mr-0.5 rounded-sm hover:bg-background/60"
              aria-label={`Remove ${t}`}
            >
              <X className="size-2.5" />
            </button>
          </Badge>
        ))}
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(input);
            } else if (e.key === "Backspace" && !input && value.length > 0) {
              remove(value[value.length - 1]);
            }
          }}
          placeholder={value.length === 0 ? "Type a tag and press Enter" : ""}
          className="h-6 flex-1 border-0 bg-transparent p-0 text-xs shadow-none focus-visible:ring-0"
        />
      </div>
      {suggestions.length > 0 && input && (
        <div className="flex flex-wrap gap-1">
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => add(s)}
              className="rounded-full border bg-background px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary hover:text-foreground"
            >
              + {s}
            </button>
          ))}
        </div>
      )}
      {description && <p className="text-[11px] text-muted-foreground">{description}</p>}
    </div>
  );
}
