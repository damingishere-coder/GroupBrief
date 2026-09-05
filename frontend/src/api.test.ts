import { afterEach, describe, expect, it, vi } from "vitest";

import { batchUpdateGroupImageTheme, confirmRecovery, get, getDashboard, getRuns, getRuntimeLogs, pipelineGenerate, pipelineSend, readV2JsonFile, resetSendFailure, resolveManualSend, resolvePromptUnknown, resolveSendUnknown } from "./api";

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

  it("sends one batch request for multiple group image themes", async () => {
    const fetchMock = vi.fn(async () => response({
      status: "success",
      requested_count: 2,
      success: [],
      failed: [],
    }));
    vi.stubGlobal("fetch", fetchMock);

    await batchUpdateGroupImageTheme({
      group_ids: [7, 8],
      image_theme: "custom",
      image_theme_custom: "低饱和黏土摄影",
      image_theme_apply_count: 3,
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/groups/batch/image-theme",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          group_ids: [7, 8],
          image_theme: "custom",
          image_theme_custom: "低饱和黏土摄影",
          image_theme_apply_count: 3,
        }),
      }),
    );
  });

  it("confirms historical generation with CAS and no send field", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      response({ status: "complete", send_invoked: false }));
    vi.stubGlobal("fetch", fetchMock);

    await confirmRecovery({
      expected_version: "a".repeat(64),
      tasks: [{ run_date: "2026-08-24", group_id: 7 }],
    });

    const options = fetchMock.mock.calls[0]?.[1];
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/recovery/confirm",
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(String(options?.body))).toEqual({
      expected_version: "a".repeat(64),
      tasks: [{ run_date: "2026-08-24", group_id: 7 }],
    });
  });

  it("sends the unknown-resolution CAS timestamp without triggering send", async () => {
    const fetchMock = vi.fn(async () => response({ result: { status: "resolved" } }));
    vi.stubGlobal("fetch", fetchMock);

    await resolveSendUnknown({
      group_id: 7,
      run_date: "2026-08-26",
      resolution: "text_sent",
      expected_send_unknown_at: "2026-08-26T08:30:59+08:00",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/pipeline/resolve-send-unknown",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          group_id: 7,
          run_date: "2026-08-26",
          resolution: "text_sent",
          expected_send_unknown_at: "2026-08-26T08:30:59+08:00",
        }),
      }),
    );
  });

  it("sends the prompt-unknown operation id to the resolution-only endpoint", async () => {
    const fetchMock = vi.fn(async () => response({ result: { status: "resolved" } }));
    vi.stubGlobal("fetch", fetchMock);

    await resolvePromptUnknown({
      group_id: 7,
      run_date: "2026-08-27",
      expected_operation_id: "operation-123",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/pipeline/resolve-prompt-unknown",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          group_id: 7,
          run_date: "2026-08-27",
          expected_operation_id: "operation-123",
        }),
      }),
    );
  });

  it("resets only a CAS-matched explicit send failure without calling send", async () => {
    const fetchMock = vi.fn(async () => response({ result: { status: "prepared" } }));
    vi.stubGlobal("fetch", fetchMock);

    await resetSendFailure({
      group_id: 24,
      run_date: "2026-08-29",
      expected_updated_at: "2026-08-29 08:38:00",
      expected_state_version: 17,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/pipeline/reset-send-failure",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          group_id: 24,
          run_date: "2026-08-29",
          expected_updated_at: "2026-08-29 08:38:00",
          expected_state_version: 17,
        }),
      }),
    );
  });

  it("requests the selected dashboard run date", async () => {
    const fetchMock = vi.fn(async () => response({ cards: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await getDashboard("2026-08-25");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/dashboard?run_date=2026-08-25",
      expect.any(Object),
    );
  });

  it("requests safe structured runtime logs with explicit filters", async () => {
    const fetchMock = vi.fn(async () => response({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await getRuntimeLogs("2026-08-25", {
      tail: 100,
      sources: "provider",
      levels: "WARNING",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/runtime/logs?run_date=2026-08-25&tail=100&sources=provider&levels=WARNING",
      expect.any(Object),
    );
  });

  it("records a whole manual send resolution with updated-at CAS", async () => {
    const fetchMock = vi.fn(async () => response({ result: { status: "resolved" } }));
    vi.stubGlobal("fetch", fetchMock);

    await resolveManualSend({
      group_id: 7,
      run_date: "2026-08-26",
      resolution: "all_sent",
      expected_updated_at: "2026-08-26 08:31:00",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/pipeline/resolve-manual-send",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          group_id: 7,
          run_date: "2026-08-26",
          resolution: "all_sent",
          expected_updated_at: "2026-08-26 08:31:00",
        }),
      }),
    );
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

  it("requests batched run files only when explicitly enabled", async () => {
    const fetchMock = vi.fn(async () => response({ runs: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await getRuns("2026-08-25", { includeFiles: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v2/runs?run_date=2026-08-25&include_files=true",
      expect.any(Object),
    );
  });
});
