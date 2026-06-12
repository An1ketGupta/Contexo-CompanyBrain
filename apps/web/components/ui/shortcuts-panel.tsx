"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./dialog";
import { useShortcutsPanel } from "./shortcuts-panel-context";

interface Shortcut {
  keys: string[];
  label: string;
  scope?: "global" | "chat";
}

const SHORTCUTS: Shortcut[] = [
  { keys: ["⌘", "K"], label: "Open command palette", scope: "global" },
  { keys: ["?"], label: "Show this panel", scope: "global" },
  { keys: ["⌘", "N"], label: "New conversation", scope: "global" },
  { keys: ["⌘", "/"], label: "Focus conversation search", scope: "global" },
  { keys: ["↵"], label: "Send message", scope: "chat" },
  { keys: ["⇧", "↵"], label: "Insert newline", scope: "chat" },
  { keys: ["Esc"], label: "Stop generating", scope: "chat" },
  { keys: ["⌘", "⇧", "C"], label: "Copy last response", scope: "chat" },
];

export function ShortcutsPanel() {
  const { open, setOpen } = useShortcutsPanel();
  // Render the swap-Cmd-for-Ctrl label only after mount so SSR doesn't differ
  // from the client. Server-rendered HTML always shows Mac glyphs; we patch
  // them in once we know the platform.
  const [isMac, setIsMac] = useState(true);
  useEffect(() => {
    if (typeof navigator !== "undefined") {
      setIsMac(/mac/i.test(navigator.platform));
    }
  }, []);

  const groups: { title: string; items: Shortcut[] }[] = [
    { title: "Global", items: SHORTCUTS.filter((s) => s.scope === "global") },
    { title: "Chat", items: SHORTCUTS.filter((s) => s.scope === "chat") },
  ];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Speed up navigation and chat with these key bindings.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 pt-1">
          {groups.map((g) => (
            <div key={g.title}>
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {g.title}
              </div>
              <ul className="space-y-1.5">
                {g.items.map((s) => (
                  <li
                    key={s.label}
                    className="flex items-center justify-between gap-3 py-0.5 text-sm"
                  >
                    <span className="text-muted-foreground">{s.label}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {s.keys.map((k, i) => (
                        <kbd
                          key={`${k}-${i}`}
                          className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                        >
                          {k === "⌘" && !isMac ? "Ctrl" : k}
                        </kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
