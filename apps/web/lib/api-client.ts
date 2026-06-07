/**
 * Thin wrapper for calling Next.js API proxy routes from client components.
 * Auth is handled server-side in each proxy route (reads from Supabase cookies).
 * Never calls FastAPI directly — that URL stays server-side only.
 */
export async function apiClient(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  return fetch(`/api${path}`, options);
}
