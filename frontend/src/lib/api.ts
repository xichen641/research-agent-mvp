export type ApiProblemDetails = Record<string, string | number | boolean | null>;

export class ApiProblem extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly traceId: string | null;
  readonly details: ApiProblemDetails;

  constructor(options: {
    status: number;
    code: string;
    message: string;
    retryable?: boolean;
    traceId?: string | null;
    details?: ApiProblemDetails;
  }) {
    super(options.message);
    this.name = "ApiProblem";
    this.status = options.status;
    this.code = options.code;
    this.retryable = options.retryable ?? false;
    this.traceId = options.traceId ?? null;
    this.details = options.details ?? {};
  }
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const SAFE_DETAIL_KEYS = new Set(["field", "reason", "limit"]);

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) {
    throw await toApiProblem(response);
  }
  return (await response.json()) as T;
}

async function toApiProblem(response: Response): Promise<ApiProblem> {
  const generic = new ApiProblem({
    status: response.status,
    code: `HTTP_${response.status}`,
    message: `请求失败（${response.status}）`,
  });
  if (!response.headers.get("content-type")?.includes("json")) {
    return generic;
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    return generic;
  }
  if (!isRecord(value)) {
    return generic;
  }
  const code = typeof value.code === "string" ? value.code : generic.code;
  const message =
    typeof value.message === "string" ? value.message : generic.message;
  const retryable =
    typeof value.retryable === "boolean" ? value.retryable : false;
  const traceId = typeof value.trace_id === "string" ? value.trace_id : null;
  return new ApiProblem({
    status: response.status,
    code,
    message,
    retryable,
    traceId,
    details: safeDetails(value.details),
  });
}

function safeDetails(value: unknown): ApiProblemDetails {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      ([key, item]) =>
        SAFE_DETAIL_KEYS.has(key) &&
        (item === null ||
          typeof item === "string" ||
          typeof item === "number" ||
          typeof item === "boolean"),
    ),
  ) as ApiProblemDetails;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
