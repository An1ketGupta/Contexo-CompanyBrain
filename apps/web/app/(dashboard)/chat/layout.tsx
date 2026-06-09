import type { ReactNode } from "react";

export default function ChatLayout({ children }: { children: ReactNode }) {
  // Pin the chat shell to the dashboard frame; ConversationSidebar + main panel
  // are siblings inside this row container. The dashboard layout already
  // handles the outer left-rail navigation.
  return <div className="flex h-full min-h-0 w-full flex-row">{children}</div>;
}
