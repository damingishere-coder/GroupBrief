import { useEffect, useRef } from "react";

export function useReportScroll(group: string | null) {
  const previous = useRef(group);
  const galleryScroll = useRef(0);
  useEffect(() => {
    if (previous.current === group) return;
    const wasOpen = Boolean(previous.current);
    previous.current = group;
    const frame = window.requestAnimationFrame(() => {
      if (group)
        document
          .querySelector(".report-workspace")
          ?.scrollIntoView({ block: "start" });
      else if (wasOpen) window.scrollTo({ top: galleryScroll.current });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [group]);
  return () => {
    if (!group) galleryScroll.current = window.scrollY;
  };
}
