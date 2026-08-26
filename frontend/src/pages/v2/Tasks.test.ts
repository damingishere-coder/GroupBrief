import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "../../components/common";
import { sendResolutionDialogCopy } from "./Tasks";

describe("send unknown resolution confirmations", () => {
  it.each([
    ["text_sent" as const, "确认文字已经发送？", "之后只允许继续图片阶段"],
    ["not_sent" as const, "确认文字没有发送？", "之后可重新执行完整发送"],
  ])("renders a confirmation before writing %s", (resolution, title, consequence) => {
    const copy = sendResolutionDialogCopy(resolution);
    const markup = renderToStaticMarkup(
      createElement(ConfirmDialog, {
        open: true,
        title: copy.title,
        description: copy.description,
        confirmLabel: "写入核对结论",
        onConfirm: vi.fn(),
        onCancel: vi.fn(),
      }),
    );

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain(title);
    expect(markup).toContain("不会立即发送");
    expect(markup).toContain(consequence);
    expect(markup).toContain("写入核对结论");
  });
});
