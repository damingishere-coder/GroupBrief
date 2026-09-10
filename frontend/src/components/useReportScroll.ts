import { useEffect, useRef } from "react";

export function useReportScroll(group: string | null, scrollOnInitialOpen = true) {
  const previous = useRef(group);
  const requested = useRef(false);
  const galleryScroll = useRef(0);
  useEffect(() => {
    if (previous.current === group) return;
    const wasOpen = Boolean(previous.current);
    const shouldScroll = scrollOnInitialOpen || requested.current;
    requested.current = false;
    previous.current = group;
    const frame = window.requestAnimationFrame(() => {
      if (group && shouldScroll)
        document
          .querySelector(".report-workspace")
          ?.scrollIntoView({ block: "start" });
      else if (!group && wasOpen) window.scrollTo({ top: galleryScroll.current });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [group, scrollOnInitialOpen]);
  return () => {
    requested.current = true;
    if (!group) galleryScroll.current = window.scrollY;
  };
}
