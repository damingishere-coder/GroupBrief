import { useEffect, useRef } from "react";
import { registerLeaveGuard } from "../navigation";

/** Keep drafts and the target of an in-flight mutation attached to their report. */
export function useUnsavedChanges(dirty: boolean, busy = false) {
  const state = useRef({ dirty, busy });
  state.current = { dirty, busy };
  useEffect(() => {
    const guard = () => {
      if (state.current.busy) return false;
      return (
        !state.current.dirty ||
        window.confirm("有未保存的修改。离开将丢弃这些修改，确定离开吗？")
      );
    };
    const release = registerLeaveGuard(guard);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (state.current.dirty || state.current.busy) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      release();
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, []);
  return () => {
    state.current = { dirty: false, busy: false };
  };
}
