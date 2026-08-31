// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "../../components/common";
import { sendResolutionDialogCopy } from "./Tasks";

describe("send unknown resolution confirmations", () => {
  beforeAll(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
  });

  afterAll(() => vi.unstubAllGlobals());

  it.each([
    ["text_sent" as const, "确认文字已经发送？", "之后只允许继续图片阶段"],
    ["not_sent" as const, "确认文字没有发送？", "之后可重新执行完整发送"],
  ])("renders a confirmation before writing %s", (resolution, title, consequence) => {
    const copy = sendResolutionDialogCopy(resolution);
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(createElement(ConfirmDialog, {
        open: true,
        title: copy.title,
        description: copy.description,
        confirmLabel: "写入核对结论",
        onConfirm: vi.fn(),
        onCancel: vi.fn(),
      }));
    });
    const markup = container.innerHTML;

    expect(markup).toContain('role="dialog"');
    expect(markup).toContain(title);
    expect(markup).toContain("不会立即发送");
    expect(markup).toContain(consequence);
    expect(markup).toContain("写入核对结论");
    act(() => root.unmount());
    container.remove();
  });
});
