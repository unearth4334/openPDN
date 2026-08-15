/**
 * The single place the UI talks to the backend.
 *
 * Components call these functions; none of them call `fetch` directly. That
 * keeps error handling, base paths and future auth in one file.
 */

import type {
  BoardListResponse,
  BoardReviewResponse,
  DevFixtureResponse,
  GeometryResponse,
  GeometryViewName,
  HealthResponse,
  InfoResponse,
} from "./types";

/** Same-origin in production; the Vite dev server proxies /api in development. */
const API_BASE = "/api";

/** A failed API call, carrying the status so callers can distinguish causes. */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number, options?: ErrorOptions) {
    super(message, options);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    // A network failure is the normal case when the backend is not running;
    // say so plainly rather than surfacing a raw TypeError. The original error
    // is kept as `cause` so a real network fault stays debuggable.
    throw new ApiError(`Cannot reach the openPDN API at ${API_BASE}${path}`, 0, { cause });
  }

  if (!response.ok) {
    throw new ApiError(`GET ${path} failed with ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

async function readError(response: Response, fallback: string): Promise<ApiError> {
  // The backend answers failures as {error, detail}; surface the detail so an
  // import problem reads as a parser diagnosis, never a bare status code.
  try {
    const payload = (await response.json()) as { error?: string; detail?: string };
    if (payload.detail) {
      return new ApiError(payload.detail, response.status);
    }
  } catch {
    // Not JSON; fall through to the generic message.
  }
  return new ApiError(fallback, response.status);
}

/** Liveness and build identity. */
export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJson<HealthResponse>("/health", signal);
}

/** Solvers, importers and honest capability statuses for this deployment. */
export function fetchInfo(signal?: AbortSignal): Promise<InfoResponse> {
  return getJson<InfoResponse>("/info", signal);
}

/** Upload a PCB source file and return its import review. */
export async function importBoard(file: File, signal?: AbortSignal): Promise<BoardReviewResponse> {
  const body = new FormData();
  body.append("file", file, file.name);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/boards`, {
      method: "POST",
      body,
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    throw new ApiError(`Cannot reach the openPDN API at ${API_BASE}/boards`, 0, { cause });
  }
  if (!response.ok) {
    throw await readError(response, `Import failed with HTTP ${response.status}`);
  }
  return (await response.json()) as BoardReviewResponse;
}

/** Boards currently held by the backend. */
export function fetchBoards(signal?: AbortSignal): Promise<BoardListResponse> {
  return getJson<BoardListResponse>("/boards", signal);
}

/** Full review of one stored board. */
export function fetchBoard(boardId: string, signal?: AbortSignal): Promise<BoardReviewResponse> {
  return getJson<BoardReviewResponse>(`/boards/${encodeURIComponent(boardId)}`, signal);
}

/** One geometry view; large, fetched once per view and cached by the caller. */
export function fetchGeometry(
  boardId: string,
  view: GeometryViewName,
  signal?: AbortSignal,
): Promise<GeometryResponse> {
  return getJson<GeometryResponse>(
    `/boards/${encodeURIComponent(boardId)}/geometry?view=${view}`,
    signal,
  );
}

/** Development-only: the locally configured fixture, when the backend offers one. */
export function fetchDevFixture(signal?: AbortSignal): Promise<DevFixtureResponse> {
  return getJson<DevFixtureResponse>("/dev/fixture", signal);
}

/** Development-only: import the locally configured fixture. */
export async function importDevFixture(signal?: AbortSignal): Promise<BoardReviewResponse> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/dev/fixture/import`, {
      method: "POST",
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    throw new ApiError(`Cannot reach the openPDN API at ${API_BASE}/dev/fixture/import`, 0, {
      cause,
    });
  }
  if (!response.ok) {
    throw await readError(response, `Fixture import failed with HTTP ${response.status}`);
  }
  return (await response.json()) as BoardReviewResponse;
}
