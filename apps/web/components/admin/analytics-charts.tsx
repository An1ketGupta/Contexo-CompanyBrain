"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface DailyPoint {
  day: string;
  count: number;
}

export interface IntentPoint {
  intent: string;
  count: number;
}

const INTENT_LABELS: Record<string, string> = {
  task_generation: "Writing",
  factual_qa: "Q&A",
  analysis: "Analysis",
  search: "Search",
};

const formatDate = (iso: unknown): string => {
  if (typeof iso !== "string") return "";
  return new Date(iso).toLocaleDateString("en", {
    month: "short",
    day: "numeric",
  });
};

const formatDateLong = (iso: unknown): string => {
  if (typeof iso !== "string") return "";
  return new Date(iso).toLocaleDateString("en", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
};

// Recharts' tooltip formatter signatures are intentionally loose to support
// every chart type. Cast at the boundary so the chart specifics stay tidy.
const queryFormatter = (value: unknown): [string, string] => [
  String(value ?? 0),
  "Queries",
];

export function DailyQueriesChart({ data }: { data: DailyPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 5, right: 12, left: 0, bottom: 5 }}>
        <defs>
          <linearGradient id="queryGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--primary)" stopOpacity={0.25} />
            <stop offset="95%" stopColor="var(--primary)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid
          stroke="var(--border)"
          strokeDasharray="3 3"
          vertical={false}
        />
        <XAxis
          dataKey="day"
          tickFormatter={formatDate}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          minTickGap={24}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={28}
          allowDecimals={false}
        />
        <Tooltip
          contentStyle={{
            fontSize: 12,
            borderRadius: 8,
            background: "var(--popover)",
            border: "1px solid var(--border)",
            color: "var(--popover-foreground)",
          }}
          labelFormatter={formatDateLong}
          formatter={queryFormatter}
        />
        <Area
          type="monotone"
          dataKey="count"
          stroke="var(--primary)"
          strokeWidth={2}
          fill="url(#queryGradient)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function IntentBreakdownChart({ data }: { data: IntentPoint[] }) {
  const rendered = data.map((d) => ({
    ...d,
    label: INTENT_LABELS[d.intent] ?? d.intent,
  }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={rendered} layout="vertical" margin={{ left: 8, right: 12 }}>
        <CartesianGrid
          stroke="var(--border)"
          strokeDasharray="3 3"
          horizontal={false}
        />
        <XAxis
          type="number"
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          allowDecimals={false}
        />
        <YAxis
          type="category"
          dataKey="label"
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          tickLine={false}
          axisLine={false}
          width={72}
        />
        <Tooltip
          contentStyle={{
            fontSize: 12,
            borderRadius: 8,
            background: "var(--popover)",
            border: "1px solid var(--border)",
            color: "var(--popover-foreground)",
          }}
          formatter={queryFormatter}
        />
        <Bar dataKey="count" fill="var(--primary)" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
