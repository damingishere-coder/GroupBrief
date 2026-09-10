import { useEffect, useState } from "react";
import { GearSix, PaperPlaneTilt, WarningCircle } from "@phosphor-icons/react";
import {
  type DashboardCard,
  pipelineGenerate,
  pipelineSend,
  resolveManualSend,
  resolvePromptUnknown,
} from "../../api";
import { Button, ConfirmDialog, Toast } from "../../components/common";
import { useToast } from "../../components/ui";
import { navigateToHash } from "../../navigation";
interface TaskActionsProps {
  card: DashboardCard;
  generating: boolean;
  sending: boolean;
  senderOk: boolean;
  senderDetail: string;
  onGenerate: () => void;
  onResolvePrompt: () => void;
  onSend: () => void;
  onResolve: () => void;
}

function TaskActions({
  card,
  generating,
  sending,
  senderOk,
  senderDetail,
  onGenerate,
  onResolvePrompt,
  onSend,
  onResolve,
}: TaskActionsProps) {
  const canGenerate = card.status !== "SENT" && !card.prompt_hold;
  const canResolvePrompt =
    card.prompt_hold && card.prompt_hold_reason === "PROMPT_RESULT_UNKNOWN";
  const canSend =
    ["IMAGE_READY", "READY_TO_SEND"].includes(card.status) &&
    !card.sent_at &&
    !card.send_hold &&
    card.image_delivery_eligible !== false;

  return (
    <div className="dashboard-task-actions">
      <Button
        tone="ghost"
        className="ui-button-compact"
        onClick={() => navigateToHash(`/groups/${card.group_id}`)}
      >
        <GearSix size={16} aria-hidden="true" />
        查看配置
      </Button>
      {canGenerate && (
        <Button
          tone="secondary"
          className="ui-button-compact"
          onClick={onGenerate}
          busy={generating}
          title={
            generating ? "正在生成中，请耐心等待" : "立即生成当前选择日期的日报"
          }
        >
          {generating ? "生成中…" : "立即生成"}
        </Button>
      )}
      {canResolvePrompt && (
        <Button
          tone="secondary"
          className="ui-button-compact"
          onClick={onResolvePrompt}
        >
          <WarningCircle size={16} aria-hidden="true" />
          核对后重试
        </Button>
      )}
      {card.prompt_hold && !canResolvePrompt && (
        <Button
          tone="ghost"
          className="ui-button-compact"
          disabled
          title="当前 Prompt 暂停原因需要人工检查运行记录"
        >
          <WarningCircle size={16} aria-hidden="true" />
          Prompt 待复核
        </Button>
      )}
      {canSend && senderOk && (
        <Button
          tone="primary"
          className="ui-button-compact"
          onClick={onSend}
          busy={sending}
        >
          <PaperPlaneTilt size={16} aria-hidden="true" />
          立即发送
        </Button>
      )}
      {canSend && !senderOk && (
        <Button
          tone="ghost"
          className="ui-button-compact"
          disabled
          title={senderDetail || "微信自动发送服务不可用"}
        >
          微信发送未启用
        </Button>
      )}
      {card.send_hold && card.image_delivery_eligible !== false && (
        <Button
          tone="secondary"
          className="ui-button-compact"
          onClick={onResolve}
        >
          <WarningCircle size={16} aria-hidden="true" />
          人工核对
        </Button>
      )}
    </div>
  );
}

export function ReportActions({
  card,
  runDate,
  senderOk,
  senderDetail,
  disabled,
  onUpdated,
  onBusyChange,
}: {
  card: DashboardCard;
  runDate: string;
  senderOk: boolean;
  senderDetail: string;
  disabled: boolean;
  onUpdated: () => void;
  onBusyChange: (busy: boolean) => void;
}) {
  const { msg, toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<"send" | "prompt" | "manual" | null>(
    null,
  );
  const [confirmation, setConfirmation] = useState({ card, runDate });
  const openDialog = (kind: "send" | "prompt" | "manual") => {
    setConfirmation({ card: { ...card }, runDate });
    setDialog(kind);
  };
  const [resolution, setResolution] = useState<
    "all_sent" | "text_sent" | "not_sent"
  >("all_sent");
  useEffect(() => {
    onBusyChange(busy || Boolean(dialog));
  }, [busy, dialog, onBusyChange]);
  useEffect(() => () => onBusyChange(false), [onBusyChange]);
  const perform = async (action: "generate" | "send" | "prompt" | "manual") => {
    if (busy || disabled) return;
    const target = action === "generate" ? { card, runDate } : confirmation;
    const actionCard = target.card;
    setBusy(true);
    try {
      if (action === "send") {
        const response = await pipelineSend({
          group_id: actionCard.group_id,
          run_date: target.runDate,
        });
        const result = response.result;
        toast(
          result && result.status.toLowerCase() === "sent"
            ? `「${actionCard.group_name}」已发送`
            : `发送未完成：${result?.detail || result?.error || result?.error_type || result?.status || "未知状态"}`,
        );
      } else if (action === "manual") {
        const response = await resolveManualSend({
          group_id: actionCard.group_id,
          run_date: target.runDate,
          resolution,
          expected_updated_at: actionCard.updated_at,
        });
        toast(response.result.detail);
      } else {
        if (action === "prompt")
          await resolvePromptUnknown({
            group_id: actionCard.group_id,
            run_date: target.runDate,
            expected_operation_id: actionCard.prompt_operation_id,
          });
        const response = await pipelineGenerate({
          group_id: actionCard.group_id,
          force: true,
          run_date: target.runDate,
        });
        const result = response.results?.[0];
        toast(
          !result
            ? "生成接口未返回结果"
            : `生成状态：${result.detail || result.error_type || result.status}`,
        );
      }
      onUpdated();
      setDialog(null);
    } catch (error) {
      toast(`操作未完成：${String(error)}`);
      onUpdated();
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <fieldset className="report-action-fieldset" disabled={disabled || busy}>
        <TaskActions
          card={card}
          generating={busy}
          sending={busy}
          senderOk={senderOk && card.wechat_send_enabled}
          senderDetail={senderDetail}
          onGenerate={() => void perform("generate")}
          onResolvePrompt={() => openDialog("prompt")}
          onSend={() => openDialog("send")}
          onResolve={() => {
            setResolution("all_sent");
            openDialog("manual");
          }}
        />
      </fieldset>
      <ConfirmDialog
        open={dialog === "send"}
        title="确认立即发送"
        description={`将把「${confirmation.card.group_name}」在 ${confirmation.runDate} 的排行榜文字和图片发送到配置的微信群。请确认群名、日期与内容。`}
        confirmLabel="确认发送"
        busy={busy}
        onConfirm={() => void perform("send")}
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === "prompt"}
        title="确认丢弃未知 Prompt 结果并重试"
        description={`「${confirmation.card.group_name}」上一次调用结果未知。确认后将解除暂停并发起一次新的文本生成，可能增加一次模型用量；不会发送微信或邮件。`}
        confirmLabel="确认并重新生成"
        busy={busy}
        onConfirm={() => void perform("prompt")}
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === "manual"}
        title={`核对「${confirmation.card.group_name}」的发送结果`}
        description="请选择你已经在微信中实际完成的情况。这里仅更新任务状态和审计记录，不会再次发送任何内容。"
        confirmLabel="确认核对结果"
        busy={busy}
        onConfirm={() => void perform("manual")}
        onCancel={() => setDialog(null)}
      >
        <fieldset className="manual-resolution-options">
          {(
            [
              ["all_sent", "排行榜和图片均已发送"],
              ["text_sent", "只发送了排行榜文字"],
              ["not_sent", "两项都没有发送"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              <input
                type="radio"
                name="manual-resolution"
                checked={resolution === key}
                onChange={() => setResolution(key)}
              />
              {label}
            </label>
          ))}
        </fieldset>
      </ConfirmDialog>
      <Toast message={msg} />
    </>
  );
}
