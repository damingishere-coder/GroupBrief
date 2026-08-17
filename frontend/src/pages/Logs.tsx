import { useState } from "react";
import { get } from "../api";
import { useFetch } from "../components/ui";

export default function Logs() {
  const { data: files } = useFetch(() =>
    get<{ name: string; size: number }[]>("/logs/files")
  );
  const [active, setActive] = useState("app.log");
  const [content, setContent] = useState("");
  const [error, setError] = useState("");

  const load = async (name: string) => {
    setActive(name);
    try {
      const text = await get<unknown>(`/logs/${name}?tail=300`);
      setContent(typeof text === "string" ? text : String(text));
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-title">日志</div>
        <div className="page-sub">分类日志（不含敏感信息）</div>
      </div>
      <div className="tabs" style={{ marginBottom: 16 }}>
        {(files ?? []).map((f) => (
          <button
            key={f.name}
            className={`tab ${active === f.name ? "active" : ""}`}
            onClick={() => load(f.name)}
          >
            {f.name}
            {f.size > 0 ? `（${Math.round(f.size / 1024)}KB）` : "（空）"}
          </button>
        ))}
      </div>
      <div className="card">
        {error ? (
          <div className="muted">{error}</div>
        ) : (
          <pre className="panel" style={{ maxHeight: 600 }}>
            {content || "（空）"}
          </pre>
        )}
      </div>
    </div>
  );
}
