import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the parsed body on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    const health = await api.health();
    expect(health).toEqual({ status: "ok" });
    const [url] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/health");
  });

  it("throws an ApiError carrying the server's detail message on failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "invalid email or password" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await api.login("a@b.com", "wrong");
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ status: 401, detail: "invalid email or password" });
  });

  it("falls back to the status text when the error body isn't JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("<html>502</html>", { status: 502, statusText: "Bad Gateway" })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.health()).rejects.toMatchObject({ status: 502, detail: "Bad Gateway" });
  });

  it("sends the bearer token and query params it was given", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);

    await api.incidents("tok-123", { verdict: "cyberattack", limit: 5 });
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(String(url)).toContain("verdict=cyberattack");
    expect(String(url)).toContain("limit=5");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer tok-123");
  });

  it("builds a ws:// URL with the token as a query param", () => {
    expect(api.wsUrl("sess-1", "tok")).toBe(
      "ws://localhost:8000/api/v1/ws/sessions/sess-1?token=tok"
    );
    expect(api.wsUrl("sess-1", null)).toBe("ws://localhost:8000/api/v1/ws/sessions/sess-1");
  });
});
