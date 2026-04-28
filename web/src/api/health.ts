const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";

export type HealthStatus = "ok" | "error";

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch(`${baseUrl}/health`);
  return response.ok ? "ok" : "error";
}
