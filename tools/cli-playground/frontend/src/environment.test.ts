import { describe, expect, it } from "vite-plus/test";
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
  pr: 42,
  expires_at: "2030-01-02T03:04:05+00:00",
};
const now = Date.parse("2030-01-01T00:00:00Z");

describe("environment metadata", () => {
  it("loads all revision CLIs in one PR environment", () => {
    expect(parseEnvironment(metadata, now)).toEqual({
      id: identifier,
      clis: ["catr", "echor"],
      sha: "a".repeat(40),
      pr: 42,
      expiresAt: new Date("2030-01-02T03:04:05Z"),
    });
  });

  it.each(
    [
      null,
      [],
      {},
      { ...metadata, id: "../../outside" },
      { ...metadata, clis: [] },
      { ...metadata, clis: "catr" },
      { ...metadata, clis: ["catr", "catr"] },
      { ...metadata, clis: ["catr; echo unsafe"] },
      { ...metadata, clis: [42] },
      { ...metadata, sha: "short" },
      { ...metadata, pr: 0 },
      { ...metadata, pr: 1.5 },
      { ...metadata, pr: Number.MAX_SAFE_INTEGER + 1 },
      { ...metadata, expires_at: null },
      { ...metadata, expires_at: 2030 },
      { ...metadata, expires_at: "invalid" },
    ].map((value) => ({ value })),
  )("rejects malformed metadata: $value", ({ value }) => {
    expect(() => parseEnvironment(value, now)).toThrow(EnvironmentError);
  });

  it("does not offer retry for an expired build", () => {
    expect(() => parseEnvironment(metadata, Date.parse(metadata.expires_at))).toThrow(
      expect.objectContaining({ retryable: false }),
    );
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
