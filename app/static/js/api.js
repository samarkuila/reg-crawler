// Thin fetch wrapper for /api/v1. Shows an error toast on failure unless { silent: true }.
// No business logic: the API decides, this only transports and reports.

import { toast } from "./ui.js";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, { status = 0, detail = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Turn a FastAPI error body (string, {detail}, or validation list) into plain text. */
function describeError(status, body) {
  const detail = body && typeof body === "object" && !Array.isArray(body) ? (body.detail ?? body.message) : body;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d) => {
        if (!d || !d.msg) return String(d);
        const field = (d.loc || []).slice(1).join(".");
        return field ? `${field}: ${d.msg}` : d.msg;
      })
      .join("; ");
  }
  if (status === 0) return "Could not reach the server. Check your connection and try again.";
  if (status >= 500) return "The server hit a problem. Please try again.";
  return `Request failed (${status}).`;
}

function buildUrl(path, query) {
  const url = `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.append(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/**
 * Call the API. Returns parsed JSON (or null for 204). Throws ApiError on failure.
 * options: { method, body, query, silent, headers }
 */
export async function api(path, { method = "GET", body, query, silent = false, headers = {} } = {}) {
  const init = { method, headers: { Accept: "application/json", ...headers } };
  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body;
    } else {
      init.body = JSON.stringify(body);
      init.headers["Content-Type"] = "application/json";
    }
  }
  let response;
  try {
    response = await fetch(buildUrl(path, query), init);
  } catch (cause) {
    const err = new ApiError(describeError(0), { status: 0 });
    err.cause = cause;
    if (!silent) toast(err.message, { type: "err" });
    throw err;
  }
  let data = null;
  if (response.status !== 204) {
    const text = await response.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }
  }
  if (!response.ok) {
    const err = new ApiError(describeError(response.status, data), { status: response.status, detail: data });
    if (!silent) toast(err.message, { type: "err" });
    throw err;
  }
  return data;
}

export const get = (path, query, opts = {}) => api(path, { ...opts, query });
export const post = (path, body, opts = {}) => api(path, { ...opts, method: "POST", body });
export const put = (path, body, opts = {}) => api(path, { ...opts, method: "PUT", body });
export const patch = (path, body, opts = {}) => api(path, { ...opts, method: "PATCH", body });
export const del = (path, opts = {}) => api(path, { ...opts, method: "DELETE" });
