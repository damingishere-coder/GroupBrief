import { useEffect, useState } from "react";
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

type Kind = "ranking" | "image_prompt";

const KIND_LABEL: Record<Kind, string> = {
  ranking: "排行榜模板",
  image_prompt: "生图 Prompt 模板",
};

export default function Templates() {
  const { msg, toast } = useToast();
  const [kind, setKind] = useState<Kind>("ranking");
  const [names, setNames] = useState<string[]>([]);
  const [current, setCurrent] = useState<string>("default");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState("");

  const getFn = kind === "ranking" ? getRankingTemplate : getImagePromptTemplate;
  const saveFn = kind === "ranking" ? saveRankingTemplate : saveImagePromptTemplate;
  const resetFn = kind === "ranking" ? resetRankingTemplate : resetImagePromptTemplate;
  const delFn = kind === "ranking" ? deleteRankingTemplate : deleteImagePromptTemplate;

  const loadList = (k: Kind = kind) => {
    (k === "ranking" ? listRankingTemplates() : listImagePromptTemplates())
      .then((d) => {
        setNames(d.templates);
        if (d.templates.length && !d.templates.includes(current)) setCurrent(d.templates[0]);
      })
      .catch((e) => toast(String(e)));
  };

  const loadContent = (name: string) => {
    getFn(name)
      .then((t) => {
        setCurrent(t.name);
        setContent(t.content);
        setPreview("");
      })
      .catch((e) => toast(String(e)));
  };

  const switchKind = (k: Kind) => {
    setKind(k);
    setCurrent("default");
    setContent("");
    setPreview("");
    loadList(k);
  };

  const save = () => {
    saveFn(current, content)
      .then(() => {
        toast("已保存");
        loadList();
      })
      .catch((e) => toast(String(e)));
  };

  const reset = () => {
    if (!window.confirm(`确认恢复模板「${current}」为默认内容？当前编辑内容将丢失。`)) return;
    resetFn(current)
      .then((d) => {
        setContent(d.content);
        toast("已恢复默认");
      })
      .catch((e) => toast(String(e)));
  };

  const remove = () => {
    if (current === "default") {
      toast("默认模板不可删除");
      return;
    }
    if (!window.confirm(`确认删除模板「${current}」？`)) return;
    delFn(current)
      .then(() => {
        toast("已删除");
        setCurrent("default");
        loadList();
        loadContent("default");
      })
      .catch((e) => toast(String(e)));
  };

  const doPreview = () => {
    if (kind === "image_prompt") {
      toast("生图 Prompt 模板预览将在 P4 流水线中体现，此处可先保存");
      return;
    }
    previewRankingTemplate(current, content)
      .then((d) => setPreview(d.rendered))
      .catch((e) => toast(String(e)));
  };

  useEffect(() => {
    loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">模板中心</div>
          <div className="page-sub">编辑排行榜与生图 Prompt 模板，改动即时生效</div>
        </div>
        <div className="tabs">
          <button className={`tab ${kind === "ranking" ? "active" : ""}`} onClick={() => switchKind("ranking")}>
            排行榜模板
          </button>
          <button
            className={`tab ${kind === "image_prompt" ? "active" : ""}`}
            onClick={() => switchKind("image_prompt")}
          >
            生图 Prompt 模板
          </button>
        </div>
      </div>

      <div className="tpl-layout">
        <div className="card tpl-list">
          <div className="card-title">模板列表</div>
          {names.map((n) => (
            <button
              key={n}
              className={`tpl-item ${n === current ? "active" : ""}`}
              onClick={() => loadContent(n)}
            >
              {n}
              {n === "default" && <span className="muted">（默认）</span>}
            </button>
          ))}
        </div>
        <div className="card tpl-editor">
          <div className="tpl-head">
            <span>
              {KIND_LABEL[kind]}：<b>{current}</b>
            </span>
            <div className="row-actions">
              <button className="btn btn-sm btn-secondary" onClick={doPreview}>
                预览
              </button>
              <button className="btn btn-sm btn-secondary" onClick={reset}>
                恢复默认
              </button>
              <button className="btn btn-sm btn-danger" onClick={remove}>
                删除
              </button>
              <button className="btn btn-sm" onClick={save}>
                保存
              </button>
            </div>
          </div>
          <textarea
            className="tpl-textarea"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
          />
          {preview && (
            <div className="tpl-preview">
              <div className="muted">预览：</div>
              <pre>{preview}</pre>
            </div>
          )}
        </div>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
