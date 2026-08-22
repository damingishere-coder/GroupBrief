import { useEffect, useState } from "react";
import {
  ArrowCounterClockwise,
  Eye,
  FileText,
  FloppyDisk,
  Trash,
} from "@phosphor-icons/react";
import {
  ConfirmDialog,
  Button,
  EmptyState,
  LoadingState,
  PageHeader,
  StatusBadge,
  Toast,
} from "../../components/common";
import {
  deleteImagePromptTemplate,
  deleteRankingTemplate,
  getImagePromptTemplate,
  getRankingTemplate,
  listImagePromptTemplates,
  listRankingTemplates,
  previewRankingTemplate,
  resetImagePromptTemplate,
  resetRankingTemplate,
  saveImagePromptTemplate,
  saveRankingTemplate,
} from "../../api";
import { useToast } from "../../components/ui";

export type TemplateKind = "ranking" | "image_prompt";

const KIND_LABEL: Record<TemplateKind, string> = {
  ranking: "排行榜模板",
  image_prompt: "生图 Prompt 模板",
};

interface TemplateEditorProps {
  kind: TemplateKind;
}

export function TemplateEditor({ kind }: TemplateEditorProps) {
  const { msg, toast } = useToast();
  const [names, setNames] = useState<string[]>([]);
  const [current, setCurrent] = useState("default");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState("");
  const [loading, setLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"save" | "reset" | "delete" | "preview" | "">("");
  const [confirmAction, setConfirmAction] = useState<"reset" | "delete" | null>(null);

  const loadContent = (name: string) => {
    setContentLoading(true);
    setError("");
    const request = kind === "ranking" ? getRankingTemplate(name) : getImagePromptTemplate(name);
    request
      .then((template) => {
        setCurrent(template.name);
        setContent(template.content);
        setPreview("");
      })
      .catch((reason: unknown) => setError(`模板读取失败：${String(reason)}`))
      .finally(() => setContentLoading(false));
  };

  const loadList = (preferredName?: string) => {
    setLoading(true);
    setError("");
    const request = kind === "ranking" ? listRankingTemplates() : listImagePromptTemplates();
    request
      .then((data) => {
        setNames(data.templates);
        const preferred = preferredName || current;
        const next = data.templates.includes(preferred) ? preferred : data.templates[0] || "default";
        setCurrent(next);
        loadContent(next);
      })
      .catch((reason: unknown) => setError(`模板列表加载失败：${String(reason)}`))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setCurrent("default");
    setContent("");
    setPreview("");
    loadList();
    // kind 变化时重新读取对应的真实模板列表。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  const save = () => {
    if (!current || busy) return;
    setBusy("save");
    const request = kind === "ranking" ? saveRankingTemplate(current, content) : saveImagePromptTemplate(current, content);
    request
      .then(() => {
        toast("模板已保存，下一次流水线将使用新内容");
        loadList();
      })
      .catch((reason: unknown) => toast(`保存失败：${String(reason)}`))
      .finally(() => setBusy(""));
  };

  const reset = () => {
    if (busy) return;
    setConfirmAction(null);
    setBusy("reset");
    const request = kind === "ranking" ? resetRankingTemplate(current) : resetImagePromptTemplate(current);
    request
      .then((data) => {
        setContent(data.content);
        setPreview("");
        toast("已恢复默认内容");
      })
      .catch((reason: unknown) => toast(`恢复默认失败：${String(reason)}`))
      .finally(() => setBusy(""));
  };

  const remove = () => {
    if (current === "default") {
      setConfirmAction(null);
      toast("默认模板不可删除");
      return;
    }
    if (busy) return;
    setConfirmAction(null);
    setBusy("delete");
    const request = kind === "ranking" ? deleteRankingTemplate(current) : deleteImagePromptTemplate(current);
    request
      .then(() => {
        toast("模板已删除");
        setCurrent("default");
        loadList("default");
      })
      .catch((reason: unknown) => toast(`删除失败：${String(reason)}`))
      .finally(() => setBusy(""));
  };

  const previewTemplate = () => {
    if (kind !== "ranking") {
      toast("图片 Prompt 暂无后端预览接口");
      return;
    }
    if (busy) return;
    setBusy("preview");
    previewRankingTemplate(current, content)
      .then((data) => setPreview(data.rendered))
      .catch((reason: unknown) => toast(`模板预览失败：${String(reason)}`))
      .finally(() => setBusy(""));
  };

  if (loading && names.length === 0) return <LoadingState label={`正在加载${KIND_LABEL[kind]}…`} />;
  if (error && names.length === 0) {
    return <EmptyState title={`${KIND_LABEL[kind]}加载失败`} description={error} action={<Button tone="secondary" onClick={() => loadList()}>重新加载</Button>} />;
  }

  return (
    <div className="template-editor-page">
      <PageHeader
        title={KIND_LABEL[kind]}
        description={kind === "ranking" ? "排行榜模板编辑与样例预览；预览使用固定模板样例，不代表真实群任务。" : "生图 Prompt 模板编辑；保存后在下一次流水线中生效。"}
      />
      <div className="template-editor-layout">
        <aside className="template-editor-list" aria-label={`${KIND_LABEL[kind]}列表`}>
          <div className="template-editor-list-heading"><FileText size={17} aria-hidden="true" /><span>模板列表</span><StatusBadge tone="neutral">{names.length}</StatusBadge></div>
          {names.length === 0 ? <EmptyState title="暂无模板" description="后端暂未返回模板。" /> : names.map((name) => (
            <button
              key={name}
              type="button"
              className={`template-editor-list-item ${name === current ? "is-active" : ""}`}
              aria-current={name === current ? "true" : undefined}
              onClick={() => loadContent(name)}
            >
              <span>{name}</span>
              {name === "default" && <small>默认</small>}
            </button>
          ))}
        </aside>

        <section className="template-editor-card">
          <div className="template-editor-card-head">
            <div>
              <span className="template-editor-eyebrow">正在编辑</span>
              <h2>{current}</h2>
            </div>
            <div className="template-editor-actions">
              {kind === "ranking" && <Button tone="ghost" className="ui-button-compact" onClick={previewTemplate} busy={busy === "preview"}><Eye size={16} aria-hidden="true" />预览</Button>}
              <Button tone="ghost" className="ui-button-compact" onClick={() => setConfirmAction("reset")} disabled={Boolean(busy)}><ArrowCounterClockwise size={16} aria-hidden="true" />恢复默认</Button>
              <Button tone="danger" className="ui-button-compact" onClick={() => setConfirmAction("delete")} disabled={current === "default" || Boolean(busy)}><Trash size={16} aria-hidden="true" />删除</Button>
              <Button className="ui-button-compact" onClick={save} busy={busy === "save"}><FloppyDisk size={16} aria-hidden="true" />保存</Button>
            </div>
          </div>
          {contentLoading ? <LoadingState label="正在读取模板内容…" /> : (
            <textarea
              className="template-editor-textarea"
              aria-label={`${current} 模板内容`}
              value={content}
              onChange={(event) => setContent(event.target.value)}
              spellCheck={false}
            />
          )}
          {kind === "ranking" ? (
            <div className="template-preview-box">
              <div className="template-preview-label"><Eye size={16} aria-hidden="true" />模板样例预览</div>
              <p>以下内容由后端固定样例数据渲染，仅用于检查模板，不代表所选群的真实运行结果。</p>
              {preview ? <pre>{preview}</pre> : <span className="template-preview-empty">点击“预览”查看固定样例。</span>}
            </div>
          ) : (
            <div className="template-preview-box template-preview-note">
              <FileText size={17} aria-hidden="true" />
              <span>图片 Prompt 暂无后端预览接口；保存后在下一次流水线中生效。</span>
            </div>
          )}
        </section>
      </div>
      {error && <p className="template-editor-error">{error}</p>}
      <ConfirmDialog
        open={confirmAction === "reset"}
        title="恢复默认模板？"
        description={`将把「${current}」恢复为后端默认内容，当前编辑内容会丢失。`}
        confirmLabel="恢复默认"
        busy={busy === "reset"}
        onConfirm={reset}
        onCancel={() => !busy && setConfirmAction(null)}
      />
      <ConfirmDialog
        open={confirmAction === "delete"}
        title="删除模板？"
        description={`将删除自定义模板「${current}」。此操作不可恢复，使用该模板的群配置需要另行调整。`}
        confirmLabel="确认删除"
        busy={busy === "delete"}
        onConfirm={remove}
        onCancel={() => !busy && setConfirmAction(null)}
      />
      <Toast message={msg} />
    </div>
  );
}

export default function Templates() {
  return <TemplateEditor kind="ranking" />;
}
