import { useState } from "react";
import { Clock } from "@phosphor-icons/react";
import { getSettings, saveSettings } from "../../api";
import { Button, ConfirmDialog, Toast } from "../../components/common";
import { useToast } from "../../components/ui";

export default function SendScheduleButton({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [time, setTime] = useState("");
  const [skipped, setSkipped] = useState("");
  const [error, setError] = useState("");
  const { msg, toast } = useToast();

  const edit = async () => {
    setLoading(true);
    setError("");
    try {
      const values = await getSettings();
      setTime(values.schedule_send_time);
      setSkipped(values.schedule_send_skip_dates || "");
      setOpen(true);
    } catch {
      toast("读取发送时间失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  const save = async () => {
    if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time)) {
      setError("请选择有效的发送时间");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await saveSettings({ schedule_send_time: time });
      const current = await getSettings();
      setOpen(false);
      toast(`发送时间已保存为每天 ${current.schedule_send_time}（北京时间）`);
      onSaved();
    } catch {
      setError("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };

  return <>
    <Button tone="secondary" busy={loading} onClick={() => void edit()}><Clock size={17} />发送时间</Button>
    <ConfirmDialog open={open} title="自定义发送时间" description="所有群统一使用北京时间，按顺序发送。保存后无需重启；若所选时间今天已过，从明天开始，不会立即补发。" confirmLabel="保存发送时间" busy={saving} onConfirm={() => void save()} onCancel={() => setOpen(false)}>
      <label className="settings-field">每日发送时间<input type="time" aria-label="每日发送时间" value={time} disabled={saving} onChange={(event) => setTime(event.target.value)} /></label>
      {skipped && <p>已暂停发送的日报日期：{skipped}。修改时间不会解除暂停。</p>}
      {error && <p role="alert">{error}</p>}
    </ConfirmDialog>
    <Toast message={msg} />
  </>;
}
