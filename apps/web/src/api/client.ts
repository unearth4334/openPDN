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
  EstimateResponse,
  GeometryResponse,
  GeometryViewName,
  HealthResponse,
  InfoResponse,
  JobResponse,
  QueueResponse,
  ResultLayerFields,
  ResultMetrics,
  SimulationDraftRequest,
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

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      ...(signal ? { signal } : {}),
    });
  } catch (cause) {
    throw new ApiError(`Cannot reach the openPDN API at ${API_BASE}${path}`, 0, { cause });
  }
  if (!response.ok) {
    throw await readError(response, `POST ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

/** Validate and estimate a simulation draft without queueing it. */
export function estimateSimulation(
  boardId: string,
  draft: SimulationDraftRequest,
  signal?: AbortSignal,
): Promise<EstimateResponse> {
  return postJson(`/boards/${boardId}/simulations/estimate`, draft, signal);
}

/** Queue a simulation for the orchestrator. */
export function queueSimulation(
  boardId: string,
  draft: SimulationDraftRequest,
): Promise<QueueResponse> {
  return postJson(`/boards/${boardId}/simulations`, draft);
}

/** Recent jobs, newest first. */
export function fetchJobs(signal?: AbortSignal): Promise<JobResponse[]> {
  return getJson("/jobs", signal);
}

/** Request cancellation of a queued or running job. */
export function cancelJob(jobId: string): Promise<JobResponse> {
  return postJson(`/jobs/${jobId}/cancel`, {});
}

/** Full metrics document of a completed result. */
export function fetchResultMetrics(jobId: string, signal?: AbortSignal): Promise<ResultMetrics> {
  return getJson(`/results/${jobId}/metrics`, signal);
}

/**
 * One layer's mesh and scalar fields, decoded from the documented binary
 * layout (little-endian: u32 counts, f32 points, u32 triangles, f32 fields).
 */
export async function fetchResultFields(
  jobId: string,
  layerIndex: number,
  signal?: AbortSignal,
): Promise<ResultLayerFields> {
  const response = await fetch(`${API_BASE}/results/${jobId}/fields/${layerIndex}`, {
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) {
    throw new ApiError(`Field payload ${response.status}`, response.status);
  }
  const buffer = await response.arrayBuffer();
  const header = new Uint32Array(buffer, 0, 2);
  const pointCount = header[0] ?? 0;
  const triangleCount = header[1] ?? 0;
  let offset = 8;
  const points = new Float32Array(buffer, offset, pointCount * 2);
  offset += pointCount * 8;
  const triangles = new Uint32Array(buffer, offset, triangleCount * 3);
  offset += triangleCount * 12;
  const voltage = new Float32Array(buffer, offset, pointCount);
  offset += pointCount * 4;
  const jMag = new Float32Array(buffer, offset, triangleCount);
  offset += triangleCount * 4;
  const power = new Float32Array(buffer, offset, triangleCount);
  return {
    points,
    triangles,
    voltage_v: voltage,
    j_a_per_m2: jMag,
    power_w: power,
  };
}
