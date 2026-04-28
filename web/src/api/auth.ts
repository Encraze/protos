const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";

export interface Membership {
  tenant_id: string;
  tenant_slug: string;
  tenant_name: string;
  role: string;
}

export interface CurrentUser {
  id: string;
  email: string;
  name: string | null;
  memberships: Membership[];
}

export async function fetchMe(): Promise<CurrentUser | null> {
  const response = await fetch(`${baseUrl}/auth/me`, { credentials: "include" });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(`auth/me failed: ${response.status}`);
  return (await response.json()) as CurrentUser;
}

export async function logout(): Promise<void> {
  await fetch(`${baseUrl}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
}

export function startLoginUrl(provider: "google" | "github"): string {
  return `${baseUrl}/auth/${provider}/start`;
}
