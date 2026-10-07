import axios, { AxiosError } from "axios";

// Contract §3: relative paths only — dev uses the vite proxy, docker uses nginx.
export const apiClient = axios.create({
  baseURL: "/",
  timeout: 60000,
});

/**
 * API 错误：携带后端结构化错误码（envelope 的 code/params）。
 * message 仍为人话文案（旧后端的中文/新后端的 legacy 兜底）。
 */
export class ApiRequestError extends Error {
  readonly code: string | null;
  readonly params: Record<string, unknown>;

  constructor(
    message: string,
    code: string | null = null,
    params: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiRequestError";
    this.code = code;
    this.params = params;
  }
}

/** 从任意捕获的 error 提取后端错误码（非 ApiRequestError 时为 null）。 */
export function apiErrorCode(error: unknown): string | null {
  return error instanceof ApiRequestError ? error.code : null;
}

/** 从任意捕获的 error 提取模板插值参数。 */
export function apiErrorParams(error: unknown): Record<string, unknown> {
  return error instanceof ApiRequestError ? error.params : {};
}

// 失败 envelope 的 message 可能是字符串（现行约定），也可能是
// {code, message} 对象（结构化 detail）——两种形态都解析。
interface ErrorEnvelope {
  message?: string | { code?: string; message?: string };
  code?: string;
  params?: Record<string, unknown>;
}

function parseEnvelope(data: ErrorEnvelope | undefined): ApiRequestError | null {
  if (!data) return null;
  let message = "";
  let code: string | null = null;
  let params: Record<string, unknown> = {};
  if (typeof data.message === "string") {
    message = data.message;
  } else if (data.message && typeof data.message === "object") {
    message = data.message.message ?? "";
    code = data.message.code ?? null;
  }
  if (typeof data.code === "string" && data.code) code = data.code;
  if (data.params && typeof data.params === "object") params = data.params;
  if (!message && !code) return null;
  return new ApiRequestError(message, code, params);
}

// Non-2xx responses carry the envelope's human-readable message
// (e.g. merge-guard blocks); surface it instead of the generic
// "Request failed with status code N" so callers can branch on it.
// code/params 随 ApiRequestError 透出，调用方按 code 分支（如守卫拦截）。
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ErrorEnvelope>) => {
    const parsed = parseEnvelope(error.response?.data);
    if (parsed) {
      return Promise.reject(parsed);
    }
    return Promise.reject(error);
  },
);
