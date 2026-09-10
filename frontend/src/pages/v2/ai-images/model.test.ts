import { imageDeliveryAllowed } from "./model";
import { describe, expect, it } from "vitest";

import type { GroupV2, V2Run } from "../../../api";
import {
  describeLoadError,
  formatDateTime,
  regenerationPollDelay,
  renderGroupPreview,
  runKey,
} from "./model";

describe("AI 图片视图模型", () => {
  it("优先使用稳定群 ID、微信 ID 与日期组成运行键", () => {
    const run = {
      group_name: "测试群",
      group_id: 23,
      wechat_group_id: "wx-group-23",
      run_date: "2026-08-25",
      status: "PROMPT_READY",
    } as V2Run;
    expect(runKey(run)).toBe("23\u0000wx-group-23\u00002026-08-25");
  });

  it("把旧后端 404 转成可操作的提示", () => {
    expect(describeLoadError("主题目录", new Error('{"detail":"Not Found"}')))
      .toContain("请重启 GroupBrief 服务后重试");
    expect(describeLoadError("主题目录", new Error("timeout")))
      .toBe("主题目录加载失败：timeout");
  });

  it("移除内部注释并渲染群名和风格预览变量", () => {
    const group = {
      display_name: "测试群",
      wechat_group_name: "",
    } as GroupV2;
    const preview = renderGroupPreview(
      "<!-- internal -->\n{{group_name}} / {{image_theme}} / {{report_date}}",
      group,
      "低饱和黏土摄影",
    );
    expect(preview).not.toContain("internal");
    expect(preview).toContain("测试群 / 低饱和黏土摄影 / 统计日期（从统计周期自动填入）");
  });

  it("将 ISO 时间压缩为页面使用的分钟精度", () => {
    expect(formatDateTime("2026-08-25T08:12:59+08:00")).toBe("2026-08-25 08:12");
    expect(formatDateTime(null)).toBe("—");
  });

  it("轮询失败时指数退避并限制在 30 秒", () => {
    expect(regenerationPollDelay("running", 0)).toBe(2000);
    expect(regenerationPollDelay("running", 1)).toBe(4000);
    expect(regenerationPollDelay("running", 10)).toBe(30_000);
    expect(regenerationPollDelay("fallback_queued", 1)).toBe(10_000);
  });
});


describe("raw run image delivery provenance", () => {
  const ready = { group_name: "test", run_date: "2026-09-06", status: "IMAGE_READY" };
  it("accepts generated images without requiring an enriched API field", () => {
    expect(imageDeliveryAllowed({ ...ready, image_fallback_level: 1, image_status: "success" })).toBe(true);
  });
  it.each([
    { image_delivery_eligible: false },
    { image_fallback_level: 3 },
    { image_fallback_level: "invalid" },
    { image_variant: " Pillow " },
    { image_status: "failed" },
    { image_job: { status: "ambiguous_result" } },
    { recovery_status: "existing_output_reused", last_error_summary: "fallback=L3" },
  ])("blocks diagnostic or uncertain image provenance: %j", (metadata) => {
    expect(imageDeliveryAllowed({ ...ready, ...metadata })).toBe(false);
  });
});
