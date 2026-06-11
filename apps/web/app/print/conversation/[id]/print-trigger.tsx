"use client";

export function PrintTrigger() {
  return (
    <button
      type="button"
      onClick={() => window.print()}
      className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800"
    >
      Print / Save PDF
    </button>
  );
}
