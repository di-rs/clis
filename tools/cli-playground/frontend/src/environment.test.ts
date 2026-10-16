import { describe, expect, it, vi } from "vite-plus/test";
import {
  EnvironmentError,
  environmentIdFromPath,
  loadEnvironment,
  parseEnvironment,
} from "./environment";

const identifier = "a".repeat(32);
const metadata = {
  id: identifier,
  clis: ["catr", "echor"],
  sha: "a".repeat(40),
  revision: "a".repeat(40),
  build: null,
  pr: 42,
  expires_at: "2030-01-02T03:04:05+00:00",
};

describe("environment metadata", () => {
  it("loads all revision CLIs in one PR environment", () => {
    expect(parseEnvironment(metadata)).toEqual({
      id: identifier,
      clis: ["catr", "echor"],
      sha: "a".repeat(40),
      revision: "a".repeat(40),
      build: null,
      pr: 42,
      expiresAt: new Date("2030-01-02T03:04:05Z"),
    });
  });

  it("loads a first build before any runnable artifact exists", () => {
    const result = parseEnvironment({
      ...metadata,
      sha: null,
      revision: null,
      clis: [],
      build: {
        sha: "b".repeat(40),
        run_id: 501,
        run_number: 12,
        run_attempt: 1,
        state: "building",
      },
    });
    expect(result.sha).toBeNull();
    expect(result.revision).toBeNull();
    expect(result.clis).toEqual([]);
    expect(result.build).toEqual({
      sha: "b".repeat(40),
      state: "building",
    });
  });

  it("keeps the last runnable version separate from a failed newer head", () => {
    const result = parseEnvironment({
      ...metadata,
      revision: `${metadata.sha}-11-1`,
      build: {
        sha: "b".repeat(40),
        run_id: 501,
        run_number: 12,
        run_attempt: 2,
        state: "failed",
      },
    });
    expect(result.sha).toBe("a".repeat(40));
    expect(result.revision).toBe(`${"a".repeat(40)}-11-1`);
    expect(result.build?.sha).toBe("b".repeat(40));
    expect(result.build?.state).toBe("failed");
  });

  it.each([
    { revision: undefined },
    { revision: 42 },
    { build: undefined },
    { build: { sha: "b".repeat(40), state: "unknown" } },
    { build: { sha: null, state: "building" } },
  ])("rejects missing or malformed preview fields: %j", (value) => {
    expect(() => parseEnvironment({ ...metadata, ...value })).toThrow(EnvironmentError);
  });

  it.each(
    [
      null,
      [],
      {},
      { ...metadata, id: null },
      { ...metadata, clis: "catr" },
      { ...metadata, clis: [42] },
      { ...metadata, sha: 42 },
      { ...metadata, pr: 0 },
      { ...metadata, pr: 1.5 },
      { ...metadata, pr: Number.MAX_SAFE_INTEGER + 1 },
      { ...metadata, expires_at: null },
      { ...metadata, expires_at: 2030 },
      { ...metadata, expires_at: "invalid" },
    ].map((value) => ({ value })),
  )("rejects malformed metadata: $value", ({ value }) => {
    expect(() => parseEnvironment(value)).toThrow(EnvironmentError);
  });

  it("opens server-approved metadata even when the browser clock is ahead", async () => {
    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.parse("2040-01-01T00:00:00Z"));
    try {
      const fetcher = async () => Response.json(metadata);
      const environment = await loadEnvironment(identifier, new AbortController().signal, fetcher);
      expect(environment.revision).toBe("a".repeat(40));
      expect(environment.expiresAt.toISOString()).toBe("2030-01-02T03:04:05.000Z");
    } finally {
      clock.mockRestore();
    }
  });

  it("extracts only a valid preview route", () => {
    expect(environmentIdFromPath(`/p/${identifier}/`)).toBe(identifier);
    for (const path of ["/", "/p/", "/p/../../secret", `/p/${identifier}/more`, "/p/not-an-id"]) {
      expect(environmentIdFromPath(path)).toBeNull();
    }
  });

  it.each([404, 410])("does not retry a missing or expired link (%i)", async (status) => {
    const fetcher = async () => new Response("", { status });
    await expect(
      loadEnvironment(identifier, new AbortController().signal, fetcher),
    ).rejects.toMatchObject({ retryable: false });
  });

  it("allows retry after a temporary server failure", async () => {
    const fetcher = async () => new Response("", { status: 503 });
    await expect(
      loadEnvironment(identifier, new AbortController().signal, fetcher),
    ).rejects.toMatchObject({ retryable: true });
  });

  it("loads fresh metadata with the cancellation signal", async () => {
    const signal = new AbortController().signal;
    let request: { url: string; options?: RequestInit } | undefined;
    const fetcher = async (url: string, options?: RequestInit) => {
      request = { url, options };
      return Response.json({ ...metadata, expires_at: "2099-01-01T00:00:00Z" });
    };
    expect((await loadEnvironment(identifier, signal, fetcher)).clis).toEqual(["catr", "echor"]);
    expect(request).toEqual({
      url: `/api/environments/${identifier}`,
      options: { cache: "no-store", signal },
    });
  });

  it("rejects metadata for a different PR environment", async () => {
    const fetcher = async () =>
      Response.json({
        ...metadata,
        id: "b".repeat(32),
        expires_at: "2099-01-01T00:00:00Z",
      });
    await expect(
      loadEnvironment(identifier, new AbortController().signal, fetcher),
    ).rejects.toBeInstanceOf(EnvironmentError);
  });

  it("preserves cancellation while reading the response body", async () => {
    const error = new DOMException("Aborted", "AbortError");
    const fetcher = async () =>
      new Response(new ReadableStream({ start: (controller) => controller.error(error) }));
    await expect(loadEnvironment(identifier, new AbortController().signal, fetcher)).rejects.toBe(
      error,
    );
  });
});

it("rejects foreign or malformed page session responses", async () => {
  const { parseSession } = await import("./environment");
  const preview = "a".repeat(32),
    revision = "b".repeat(40);
  const binding = {
    id: "00000000-0000-4000-8000-000000000001",
    preview_id: preview,
    revision,
    credential: "c".repeat(43),
    absolute_deadline: "2099-01-01T01:00:00Z",
    state: "ready",
    reason: null,
  };
  expect(parseSession(binding, preview, revision)).toEqual(binding);
  for (const change of [
    { preview_id: "d".repeat(32) },
    { revision: "e".repeat(40) },
    { credential: "short" },
    { state: "unknown" },
    { absolute_deadline: "tomorrow" },
    { token: "extra" },
  ]) {
    expect(() => parseSession({ ...binding, ...change }, preview, revision)).toThrow();
  }
});
