import { describe, expect, it } from "vitest";

import type { GroupV2, V2Run } from "../../../api";
import { describeLoadError, formatDateTime, renderGroupPreview, runKey } from "./model";

describe("AI 图片视图模型", () => {
  it("使用群名与日期组成稳定且无歧义的运行键", () => {
    const run = { group_name: "测试群", run_date: "2026-08-25", status: "PROMPT_READY" } as V2Run;
    expect(runKey(run)).toBe("测试群\u00002026-08-25");
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
});
