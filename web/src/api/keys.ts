const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";

export type ProviderName = "openai" | "anthropic" | "gemini";
export type ApiKeyMode = "live" | "sandbox";

export interface ProviderKey {
  id: string;
  provider: ProviderName;
  label: string;
  last_four: string;
  revoked_at: string | null;
  created_at: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  mode: ApiKeyMode;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiKeyIssued extends ApiKey {
  token: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    credentials: "include",
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`request failed (${response.status}): ${text || response.statusText}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const providerKeysApi = {
  list: () => request<ProviderKey[]>("/v1/provider-keys"),
  create: (input: { provider: ProviderName; label: string; secret: string }) =>
    request<ProviderKey>("/v1/provider-keys", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  revoke: (id: string) =>
    request<ProviderKey>(`/v1/provider-keys/${id}/revoke`, { method: "POST" }),
};

export const apiKeysApi = {
  list: () => request<ApiKey[]>("/v1/api-keys"),
  create: (input: {
    name: string;
    scopes?: string[];
    mode?: ApiKeyMode;
    expires_at?: string | null;
  }) =>
    request<ApiKeyIssued>("/v1/api-keys", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  revoke: (id: string) =>
    request<ApiKey>(`/v1/api-keys/${id}/revoke`, { method: "POST" }),
};
