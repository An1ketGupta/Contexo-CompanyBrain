"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { BuilderShell } from "@/components/autoflows/builder-shell";
import { AUTOFLOW_TEMPLATES } from "@/lib/autoflow/templates";
import type { AutoflowDraft } from "@/lib/autoflow/types";

const BLANK: AutoflowDraft = {
  name: "",
  description: null,
  trigger_type: "document_ready",
  trigger_config: { filters: {} },
  actions: [],
  confidence_threshold: null,
  is_active: true,
};

let _id = 0;
const nid = () => `step_init_${++_id}`;

export default function NewAutoflowPage() {
  const search = useSearchParams();
  const templateId = search.get("template");
  const [initial, setInitial] = useState<AutoflowDraft>(BLANK);

  useEffect(() => {
    if (!templateId) {
      setInitial(BLANK);
      return;
    }
    const t = AUTOFLOW_TEMPLATES.find((x) => x.id === templateId);
    if (!t) {
      setInitial(BLANK);
      return;
    }
    setInitial({
      ...t.draft,
      actions: t.draft.actions.map((a) => ({ ...a, id: nid() })),
      is_active: true,
    });
  }, [templateId]);

  return <BuilderShell initial={initial} mode="create" />;
}
