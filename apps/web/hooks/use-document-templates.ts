"use client";

import { useCallback } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import type {
  DocAnalyzeResult,
  DocPreviewResult,
  DocTemplate,
  DocTemplateSchema,
  DocTemplateVariable,
  DocTemplateVersion,
  DocumentDataType,
  DocumentType,
  ReviewStatus,
} from "@/lib/types";

async function readError(res: Response, fallback: string): Promise<never> {
  const body = await res.json().catch(() => ({}));
  throw new Error(body.detail ?? body.message ?? fallback);
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) await readError(res, "Request failed");
  return res.json();
}

/** Flush every document-template cache after a mutation. Keys vary by filter,
 * so a targeted mutate would leave stale copies behind. */
function invalidateAll() {
  return globalMutate(
    (key: unknown) =>
      typeof key === "string" && key.startsWith("/api/document-templates"),
    undefined,
    { revalidate: true },
  );
}

// ── Library ────────────────────────────────────────────────────────────────

export function useDocumentTypes() {
  const { data, error, isLoading } = useSWR<DocumentType[]>(
    "/api/document-templates/types",
    getJson,
    { revalidateOnFocus: false },
  );
  return { types: data ?? [], error, isLoading };
}

export function useDocumentTemplates(includeArchived = false) {
  const key = includeArchived
    ? "/api/document-templates?include_archived=true"
    : "/api/document-templates";

  const { data, error, isLoading, mutate } = useSWR<DocTemplate[]>(
    key,
    getJson,
    { revalidateOnFocus: true, keepPreviousData: true },
  );

  const createTemplate = useCallback(
    async (input: {
      name: string;
      documentTypeId: string;
      description?: string;
      file: File;
    }): Promise<DocTemplate> => {
      const form = new FormData();
      form.append("name", input.name);
      form.append("document_type_id", input.documentTypeId);
      if (input.description) form.append("description", input.description);
      form.append("file", input.file);

      const res = await fetch("/api/document-templates", {
        method: "POST",
        body: form,
      });
      if (!res.ok) await readError(res, "Couldn't upload the template");
      const created = (await res.json()) as DocTemplate;
      await invalidateAll();
      return created;
    },
    [],
  );

  const makeDefault = useCallback(async (id: string) => {
    const res = await fetch(`/api/document-templates/${id}/default`, {
      method: "POST",
    });
    if (!res.ok) await readError(res, "Couldn't set the default");
    await invalidateAll();
  }, []);

  const archiveTemplate = useCallback(async (id: string) => {
    const res = await fetch(`/api/document-templates/${id}/archive`, {
      method: "POST",
    });
    if (!res.ok) await readError(res, "Couldn't archive the template");
    await invalidateAll();
  }, []);

  return {
    templates: data ?? [],
    error,
    isLoading,
    mutate,
    createTemplate,
    makeDefault,
    archiveTemplate,
  };
}

// ── One template ───────────────────────────────────────────────────────────

export function useDocumentTemplate(templateId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<DocTemplate>(
    templateId ? `/api/document-templates/${templateId}` : null,
    getJson,
  );
  return { template: data, error, isLoading, mutate };
}

export function useTemplateVersions(templateId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<DocTemplateVersion[]>(
    templateId ? `/api/document-templates/${templateId}/versions` : null,
    getJson,
  );

  const uploadVersion = useCallback(
    async (file: File): Promise<DocTemplateVersion> => {
      if (!templateId) throw new Error("No template selected");
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `/api/document-templates/${templateId}/versions`,
        { method: "POST", body: form },
      );
      if (!res.ok) await readError(res, "Couldn't upload the new version");
      const created = (await res.json()) as DocTemplateVersion;
      await invalidateAll();
      return created;
    },
    [templateId],
  );

  return { versions: data ?? [], error, isLoading, mutate, uploadVersion };
}

// ── The builder ────────────────────────────────────────────────────────────

export function useTemplateSchema(versionId: string | null) {
  const key = versionId
    ? `/api/document-templates/versions/${versionId}/schema`
    : null;

  const { data, error, isLoading, mutate } = useSWR<DocTemplateSchema>(
    key,
    getJson,
  );

  const analyze = useCallback(async (): Promise<DocAnalyzeResult> => {
    if (!versionId) throw new Error("No version selected");
    const res = await fetch(
      `/api/document-templates/versions/${versionId}/analyze`,
      { method: "POST" },
    );
    if (!res.ok) await readError(res, "Analysis failed");
    const result = (await res.json()) as DocAnalyzeResult;
    await mutate();
    return result;
  }, [versionId, mutate]);

  const updateVariable = useCallback(
    async (
      variableId: string,
      patch: Partial<{
        display_name: string;
        description: string;
        data_type: DocumentDataType;
        is_required: boolean;
        default_value: string;
        validation_rules: Record<string, unknown>;
        status: ReviewStatus;
      }>,
    ): Promise<DocTemplateVariable> => {
      const res = await fetch(
        `/api/document-templates/variables/${variableId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        },
      );
      if (!res.ok) await readError(res, "Couldn't update the field");
      const updated = (await res.json()) as DocTemplateVariable;
      await mutate();
      return updated;
    },
    [mutate],
  );

  const createVariable = useCallback(
    async (input: {
      internal_name: string;
      display_name: string;
      data_type: DocumentDataType;
      is_required: boolean;
      description?: string;
    }): Promise<DocTemplateVariable> => {
      if (!versionId) throw new Error("No version selected");
      const res = await fetch(
        `/api/document-templates/versions/${versionId}/variables`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input),
        },
      );
      if (!res.ok) await readError(res, "Couldn't add the field");
      const created = (await res.json()) as DocTemplateVariable;
      await mutate();
      return created;
    },
    [versionId, mutate],
  );

  const deleteVariable = useCallback(
    async (variableId: string) => {
      const res = await fetch(
        `/api/document-templates/variables/${variableId}`,
        { method: "DELETE" },
      );
      if (!res.ok && res.status !== 204) {
        await readError(res, "Couldn't remove the field");
      }
      await mutate();
    },
    [mutate],
  );

  const updateSlot = useCallback(
    async (
      slotId: string,
      patch: { status?: ReviewStatus; variable_id?: string | null },
    ) => {
      const res = await fetch(`/api/document-templates/slots/${slotId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) await readError(res, "Couldn't update the field position");
      await mutate();
    },
    [mutate],
  );

  /**
   * Attach a field to a position in the document.
   *
   * Offsets are computed by the browser against the same paragraph string the
   * renderer splices into; the backend recomputes the drift hash itself rather
   * than trusting one from the client.
   */
  const createSlot = useCallback(
    async (input: {
      variable_id: string;
      paragraph_index: number;
      start_offset: number;
      end_offset: number;
      action: "replace_span" | "insert_after_label" | "insert_empty_cell";
    }) => {
      if (!versionId) throw new Error("No version selected");
      const res = await fetch(
        `/api/document-templates/versions/${versionId}/slots`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input),
        },
      );
      if (!res.ok) await readError(res, "Couldn't place the field");
      const created = await res.json();
      await mutate();
      return created;
    },
    [versionId, mutate],
  );

  const preview = useCallback(
    async (values: Record<string, unknown> = {}): Promise<DocPreviewResult> => {
      if (!versionId) throw new Error("No version selected");
      const res = await fetch(
        `/api/document-templates/versions/${versionId}/preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ values }),
        },
      );
      if (!res.ok) await readError(res, "Couldn't build a preview");
      return res.json();
    },
    [versionId],
  );

  const downloadOriginal = useCallback(async (): Promise<string> => {
    if (!versionId) throw new Error("No version selected");
    const { url } = await getJson<{ url: string }>(
      `/api/document-templates/versions/${versionId}/download`,
    );
    return url;
  }, [versionId]);

  return {
    schema: data,
    error,
    isLoading,
    mutate,
    analyze,
    updateVariable,
    createVariable,
    deleteVariable,
    updateSlot,
    createSlot,
    preview,
    downloadOriginal,
  };
}
