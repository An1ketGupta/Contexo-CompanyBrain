"use client";

import useSWR, { mutate as globalMutate } from "swr";

export type ReportFrequency = "daily" | "weekly" | "monthly";
export type ReportType = "usage_summary" | "knowledge_health";

export interface ScheduledReport {
  id: string;
  org_id: string;
  created_by: string;
  report_type: ReportType;
  frequency: ReportFrequency;
  recipients: string[];
  send_time_utc: number;
  day_of_week: number | null;
  day_of_month: number | null;
  is_active: boolean;
  last_sent_at: string | null;
  next_send_at: string;
  created_at: string;
  updated_at: string;
}

export interface ScheduledReportInput {
  report_type: ReportType;
  frequency: ReportFrequency;
  recipients: string[];
  send_time_utc?: number;
  day_of_week?: number | null;
  day_of_month?: number | null;
  is_active?: boolean;
}

const KEY = "/api/scheduled-reports";

const fetcher = async (url: string): Promise<{ reports: ScheduledReport[] }> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export function useScheduledReports() {
  const { data, error, isLoading } = useSWR(KEY, fetcher, {
    revalidateOnFocus: false,
  });

  const reports = data?.reports ?? [];

  async function createReport(input: ScheduledReportInput): Promise<ScheduledReport> {
    const res = await fetch(KEY, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || `Failed (${res.status})`);
    }
    const row: ScheduledReport = await res.json();
    await globalMutate(KEY);
    return row;
  }

  async function updateReport(
    id: string,
    patch: Partial<ScheduledReportInput>,
  ): Promise<ScheduledReport> {
    const res = await fetch(`${KEY}/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || `Failed (${res.status})`);
    }
    const row: ScheduledReport = await res.json();
    await globalMutate(KEY);
    return row;
  }

  async function deleteReport(id: string): Promise<void> {
    const res = await fetch(`${KEY}/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) {
      throw new Error(`Failed (${res.status})`);
    }
    await globalMutate(KEY);
  }

  async function sendNow(id: string): Promise<void> {
    const res = await fetch(`${KEY}/${id}/send-now`, { method: "POST" });
    if (!res.ok) {
      throw new Error(`Failed (${res.status})`);
    }
  }

  return {
    reports,
    isLoading,
    error,
    createReport,
    updateReport,
    deleteReport,
    sendNow,
  };
}
