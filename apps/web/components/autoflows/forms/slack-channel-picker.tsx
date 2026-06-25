"use client";

import useSWR from "swr";
import { AlertTriangle, Hash, Lock } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface SlackChannel {
  id: string;
  name: string;
  is_private?: boolean;
}

interface SlackChannelPickerProps {
  value: string;
  onChange: (v: string) => void;
  description?: string;
  required?: boolean;
}

const fetcher = async (url: string): Promise<{ channels: SlackChannel[] }> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load channels (${res.status})`);
  return res.json();
};

export function SlackChannelPicker({ value, onChange, description, required }: SlackChannelPickerProps) {
  const { data, error, isLoading } = useSWR<{ channels: SlackChannel[] }>(
    "/api/integrations/slack/channels",
    fetcher,
    { revalidateOnFocus: false },
  );

  const channels = data?.channels ?? [];

  if (error) {
    return (
      <div className="space-y-1.5">
        <Label className="text-xs font-medium">
          Channel {required && <span className="text-destructive">*</span>}
        </Label>
        <div className="flex items-start gap-2 rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            Slack isn&apos;t connected. Connect it in{" "}
            <a className="underline" href="/settings/integrations" target="_blank" rel="noreferrer">
              Settings → Integrations
            </a>
            , or paste a channel ID below.
          </span>
        </div>
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="C0123456789"
          className="font-mono text-xs"
        />
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <Label className="text-xs font-medium">
        Channel {required && <span className="text-destructive">*</span>}
      </Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger>
          <SelectValue placeholder={isLoading ? "Loading channels…" : "Pick a channel"} />
        </SelectTrigger>
        <SelectContent>
          {channels.length === 0 && !isLoading ? (
            <div className="px-2 py-3 text-center text-xs text-muted-foreground">
              No channels found. Invite the bot to channels and refresh.
            </div>
          ) : (
            channels.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                <span className="inline-flex items-center gap-1.5">
                  {c.is_private ? <Lock className="size-3" /> : <Hash className="size-3" />}
                  {c.name}
                </span>
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      {description && <p className="text-[11px] text-muted-foreground">{description}</p>}
    </div>
  );
}
