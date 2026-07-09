"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Meeting } from "./upcoming-panel";

const WEEKDAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function dateKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

interface CalendarCell {
  date: Date;
  inCurrentMonth: boolean;
}

function buildMonthGrid(monthAnchor: Date): CalendarCell[] {
  const year = monthAnchor.getFullYear();
  const month = monthAnchor.getMonth();
  const firstOfMonth = new Date(year, month, 1);
  const startOffset = firstOfMonth.getDay();
  const gridStart = new Date(year, month, 1 - startOffset);

  return Array.from({ length: 42 }, (_, i) => {
    const date = new Date(gridStart);
    date.setDate(gridStart.getDate() + i);
    return { date, inCurrentMonth: date.getMonth() === month };
  });
}

export function MeetingsCalendarView({
  meetings,
  selectedDay,
  onSelectDay,
}: {
  meetings: Meeting[];
  selectedDay: string | null;
  onSelectDay: (key: string | null) => void;
}) {
  const [monthAnchor, setMonthAnchor] = useState(() => new Date());

  const meetingsByDay = useMemo(() => {
    const map = new Map<string, Meeting[]>();
    for (const m of meetings) {
      const key = dateKey(new Date(m.start_time));
      const bucket = map.get(key);
      if (bucket) bucket.push(m);
      else map.set(key, [m]);
    }
    return map;
  }, [meetings]);

  const cells = useMemo(() => buildMonthGrid(monthAnchor), [monthAnchor]);
  const todayKey = dateKey(new Date());

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <p className="text-base font-extrabold tracking-tight">
          {monthAnchor.toLocaleString(undefined, { month: "long", year: "numeric" })}
        </p>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() =>
              setMonthAnchor((d) => new Date(d.getFullYear(), d.getMonth() - 1, 1))
            }
            aria-label="Previous month"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => {
              setMonthAnchor(new Date());
              onSelectDay(null);
            }}
          >
            Today
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() =>
              setMonthAnchor((d) => new Date(d.getFullYear(), d.getMonth() + 1, 1))
            }
            aria-label="Next month"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-7 gap-1">
        {WEEKDAY_LABELS.map((label) => (
          <div
            key={label}
            className="pb-2 text-center font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground"
          >
            {label}
          </div>
        ))}

        {cells.map(({ date, inCurrentMonth }) => {
          const key = dateKey(date);
          const dayMeetings = meetingsByDay.get(key) ?? [];
          const isToday = key === todayKey;
          const isSelected = key === selectedDay;

          return (
            <button
              key={key}
              type="button"
              onClick={() => onSelectDay(isSelected ? null : key)}
              className={cn(
                "flex min-h-[68px] flex-col items-start gap-0.5 rounded-lg border p-1.5 text-left transition-colors",
                inCurrentMonth ? "border-border" : "border-transparent opacity-40",
                isSelected
                  ? "border-brand bg-brand-tint ring-1 ring-brand/30"
                  : "hover:border-input hover:bg-muted",
              )}
            >
              <span
                className={cn(
                  "text-xs font-medium",
                  isToday
                    ? "flex h-5 w-5 items-center justify-center rounded-full bg-brand font-bold text-brand-foreground"
                    : "text-muted-foreground",
                )}
              >
                {date.getDate()}
              </span>
              {dayMeetings.slice(0, 2).map((m) => (
                <span
                  key={m.id}
                  className="w-full truncate rounded-md bg-brand-tint px-1.5 py-0.5 text-[10px] font-medium text-brand"
                  title={m.title ?? "(no title)"}
                >
                  {m.title ?? "(no title)"}
                </span>
              ))}
              {dayMeetings.length > 2 && (
                <span className="px-1 text-[10px] font-medium text-muted-foreground">
                  +{dayMeetings.length - 2} more
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
