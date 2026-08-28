import { describe, expect, it } from "vitest";

import {
  formatRankingCount,
  INTERACTION_EXPLANATION,
  isTextPrimaryRanking,
  parseRanking,
  TEXT_PRIMARY_WITH_INTERACTIONS,
} from "./rankingPolicy";

describe("ranking policy display", () => {
  it("formats the text-primary policy and exposes the approved explanation", () => {
    expect(isTextPrimaryRanking(TEXT_PRIMARY_WITH_INTERACTIONS)).toBe(true);
    expect(formatRankingCount(TEXT_PRIMARY_WITH_INTERACTIONS, {
      count: 55,
      text_count: 55,
      interaction_count: 34,
    })).toBe("文字 55｜互动 34");
    expect(INTERACTION_EXPLANATION).toBe("说明：互动指图片、表情、引用等非文字消息，仅展示活跃度，不影响排名。");
  });

  it("keeps legacy rankings compatible", () => {
    expect(isTextPrimaryRanking("all_messages")).toBe(false);
    expect(formatRankingCount("all_messages", { count: 89 })).toBe("89 条");
  });

  it("falls back safely when an early strict record lacks extended counts", () => {
    expect(formatRankingCount(TEXT_PRIMARY_WITH_INTERACTIONS, { count: 12 })).toBe("文字 12｜互动 0");
  });

  it("parses extended strict ranking fields", () => {
    const parsed = parseRanking({
      group_name: "测试群",
      count_policy: TEXT_PRIMARY_WITH_INTERACTIONS,
      message_count: 3,
      speaker_count: 1,
      text_message_count: 1,
      interaction_message_count: 2,
      text_speaker_count: 1,
      top_speakers: [{
        rank: 1,
        name: "群友",
        count: 1,
        text_count: 1,
        interaction_count: 2,
        name_source: "wechat_data_analysis",
      }],
    });

    expect(parsed.error).toBe("");
    expect(parsed.summary?.countPolicy).toBe(TEXT_PRIMARY_WITH_INTERACTIONS);
    expect(parsed.summary?.topSpeakers[0]).toMatchObject({
      textCount: 1,
      interactionCount: 2,
      nameSource: "wechat_data_analysis",
    });
  });

  it("parses legacy ranking JSON without extended fields", () => {
    const parsed = parseRanking({
      group_name: "历史群",
      message_count: 8,
      speaker_count: 1,
      top_speakers: [{ rank: 1, name: "旧群友", count: 8 }],
    });

    expect(parsed.error).toBe("");
    expect(parsed.summary?.countPolicy).toBe("all_messages");
    expect(parsed.summary?.topSpeakers[0]).toMatchObject({
      count: 8,
      textCount: 8,
      interactionCount: 0,
      nameSource: "",
    });
  });
});
