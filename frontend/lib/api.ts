// API client (C7). Signs every request with API Key + HMAC (contract 3),
// subscribes to the reconnectable SSE stream, and falls back to polling.

import { readSse } from "./sse";
import { mockCreateJob, mockGetJob, mockStreamEvents } from "./mock";

export type EventType =
  | "step_started"
  | "step_succeeded"
  | "step_failed"
  | "pipeline_finished"
  | "fatal";

export interface RhEvent {
  seq: number;
  type: EventType;
  key?: string;
  provider?: string;
  content?: string;
  error?: { type: string; message: string };
}

export interface JobState {
  status: "running" | "succeeded" | "failed";
  events: RhEvent[];
}

export type ExportMode = "merged" | "steps" | "json";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";
const API_SECRET = process.env.NEXT_PUBLIC_API_SECRET ?? "";
export const USE_MOCK = (process.env.NEXT_PUBLIC_USE_MOCK ?? "") === "1";

const MAX_SSE_ATTEMPTS = 5;

// ---- HMAC signing (mirrors src/auth.py canonical exactly) -------------------
function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", bytes as any));
}

async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return toHex(sig);
}

async function signedHeaders(
  method: string,
  path: string,
  body: Uint8Array
): Promise<Record<string, string>> {
  const ts = Math.floor(Date.now() / 1000).toString();
  // canonical = METHOD + "\n" + PATH + "\n" + timestamp + "\n" + sha256_hex(body)
  const canonical = [method.toUpperCase(), path, ts, await sha256Hex(body)].join("\n");
  return {
    "X-Api-Key": API_KEY,
    "X-Timestamp": ts,
    "X-Signature": await hmacHex(API_SECRET, canonical),
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---- endpoints --------------------------------------------------------------
export async function createJob(userQuestion: string, pipeline: string): Promise<string> {
  if (USE_MOCK) return mockCreateJob(userQuestion, pipeline);
  const path = "/jobs";
  const body = new TextEncoder().encode(JSON.stringify({ user_question: userQuestion, pipeline }));
  const headers = { ...(await signedHeaders("POST", path, body)), "Content-Type": "application/json" };
  const res = await fetch(API_BASE + path, { method: "POST", headers, body });
  if (!res.ok) throw new Error(`Create run failed (${res.status}): ${await res.text()}`);
  return (await res.json()).job_id as string;
}

export async function getJob(jobId: string): Promise<JobState> {
  if (USE_MOCK) return mockGetJob(jobId);
  const path = `/jobs/${jobId}`;
  const headers = await signedHeaders("GET", path, new Uint8Array());
  const res = await fetch(API_BASE + path, { headers });
  if (!res.ok) throw new Error(`Status check failed (${res.status})`);
  return (await res.json()) as JobState;
}

/**
 * Subscribe to a job's events. Calls `onEvent` for every new event (seq order,
 * de-duplicated by seq). Reconnects with Last-Event-ID on transient drops, and
 * after repeated failures switches to polling GET /jobs/{id}. Resolves when the
 * job completes; rejects only on a fatal client error. Abort via `signal`.
 */
export async function streamEvents(
  jobId: string,
  onEvent: (event: RhEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  if (USE_MOCK) return mockStreamEvents(jobId, onEvent, signal);

  const path = `/jobs/${jobId}/events`;
  let lastSeq = 0;
  let attempts = 0;

  const deliver = (ev: RhEvent) => {
    if (typeof ev.seq === "number" && ev.seq > lastSeq) {
      lastSeq = ev.seq;
      onEvent(ev);
    }
  };

  for (;;) {
    if (signal?.aborted) return;
    try {
      const headers: Record<string, string> = await signedHeaders("GET", path, new Uint8Array());
      if (lastSeq > 0) headers["Last-Event-ID"] = String(lastSeq);
      const res = await fetch(API_BASE + path, { headers, signal });
      if (!res.ok || !res.body) throw new Error(`SSE failed (${res.status})`);

      const completed = await readSse(res.body, (frame) => {
        if (!frame.data || frame.event === "done") return;
        try {
          deliver(JSON.parse(frame.data) as RhEvent);
        } catch {
          /* ignore malformed frame */
        }
      });

      if (completed) return; // server sent `event: done`
      attempts = 0; // clean end without done -> reconnect from lastSeq
    } catch (err) {
      if (signal?.aborted) return;
      attempts += 1;
      if (attempts >= MAX_SSE_ATTEMPTS) {
        await pollUntilDone(jobId, deliver, signal);
        return;
      }
      await sleep(Math.min(1000 * attempts, 5000));
    }
  }
}

async function pollUntilDone(
  jobId: string,
  deliver: (ev: RhEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  for (;;) {
    if (signal?.aborted) return;
    try {
      const job = await getJob(jobId);
      for (const ev of job.events) deliver(ev);
      if (job.status === "succeeded" || job.status === "failed") return;
    } catch {
      /* keep polling through transient errors */
    }
    await sleep(2000);
  }
}

/** Download an export. GET /jobs/{id}/export is signed; mode is a query param. */
export async function downloadExport(jobId: string, mode: ExportMode): Promise<void> {
  const path = `/jobs/${jobId}/export`;
  const headers = await signedHeaders("GET", path, new Uint8Array());
  const res = await fetch(`${API_BASE}${path}?mode=${mode}`, { headers });
  if (!res.ok) throw new Error(`Export failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download =
    mode === "steps" ? `${jobId}_steps.zip` : mode === "json" ? `${jobId}_context.json` : `${jobId}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
