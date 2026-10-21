export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function describeDetail(payload: unknown): string {
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (item && typeof item === "object") {
            const message = (item as { msg?: unknown }).msg;
            const location = (item as { loc?: unknown }).loc;
            if (typeof message === "string") {
              const path = Array.isArray(location)
                ? location.filter((part) => part !== "body").join(".")
                : "";
              return path ? `${path}: ${message}` : message;
            }
          }
          return JSON.stringify(item);
        })
        .join("; ");
    }
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  return "Request failed";
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${API_PREFIX}${path}`;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "Network request failed. The API may be unavailable or the response did not include CORS headers.",
    );
  }
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = await response.text().catch(() => null);
    }
    throw new ApiError(response.status, describeDetail(payload));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function artifactDownloadUrl(runId: string, artifactId: string): string {
  return apiUrl(`/runs/${runId}/artifacts/${artifactId}/download`);
}
