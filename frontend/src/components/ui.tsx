import { useCallback, useEffect, useRef, useState } from "react";

export function useToast() {
  const [msg, setMsg] = useState("");
  const timer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  const toast = useCallback((text: string) => {
    setMsg(text);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(""), 2600);
  }, []);

  return { msg, toast };
}

export function copyText(text: string, toast: (s: string) => void) {
  navigator.clipboard
    .writeText(text)
    .then(() => toast("已复制到剪贴板"))
    .catch(() => toast("复制失败，请手动选择复制"));
}

export function downloadText(text: string, filename: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function useFetch<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);
  const requestId = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reload = useCallback(() => {
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    loader()
      .then((value) => {
        if (mounted.current && requestId.current === id) setData(value);
      })
      .catch((e) => {
        if (mounted.current && requestId.current === id) setError(String(e));
      })
      .finally(() => {
        if (mounted.current && requestId.current === id) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, error, loading, reload };
}
