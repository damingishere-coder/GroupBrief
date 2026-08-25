import { afterEach, describe, expect, it, vi } from "vitest";

import { get, pipelineGenerate, pipelineSend, readV2JsonFile } from "./api";

function response(body: unknown, options: { ok?: boolean; status?: number; raw?: string } = {}): Response {
  return {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    json: async () => body,
    text: async () => options.raw ?? JSON.stringify(body),
  } as Response;
}

describe("frontend API contract", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the exact generate request to the V2 endpoint", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      response({ results: [{ status: "ready_to_send" }] }));
    vi.stubGlobal("fetch", fetchMock);

    await pipelineGenerate({ group_id: 7, run_date: "2026-08-25", force: true });

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/pipeline/generate",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ group_id: 7, run_date: "2026-08-25", force: true }),
      }),
    );
  });

  it("keeps send confirmation fields in the request body", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      response({ result: { status: "sent" } }));
    vi.stubGlobal("fetch", fetchMock);

    await pipelineSend({
      group_id: 7,
      run_date: "2026-08-25",
      confirm_regenerated: true,
      confirm_late_send: true,
    });

    const options = fetchMock.mock.calls[0]?.[1];
    expect(options).toBeDefined();
    expect(JSON.parse(String(options?.body))).toEqual({
      group_id: 7,
      run_date: "2026-08-25",
      confirm_regenerated: true,
      confirm_late_send: true,
    });
  });

  it("surfaces structured FastAPI failures instead of reporting success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response({}, {
        ok: false,
        status: 410,
        raw: JSON.stringify({ detail: { code: "LEGACY_V1_WRITE_BLOCKED" } }),
      })),
    );

    await expect(get("/reports/latest")).rejects.toThrow("LEGACY_V1_WRITE_BLOCKED");
  });

  it("rejects malformed archived JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({}, { raw: "not-json" })));
    await expect(readV2JsonFile("测试群", "2026-08-25", "run.json")).rejects.toThrow(
      "run.json 不是有效的 JSON 文件",
    );
  });
});
