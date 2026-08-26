import { AnimatePresence, domAnimation, LazyMotion, m, MotionConfig } from "motion/react";
import type { ReactNode } from "react";

export const MOTION_EASE = [0.22, 1, 0.36, 1] as const;

export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <MotionConfig reducedMotion="user" transition={{ duration: 0.2, ease: MOTION_EASE }}>
      <LazyMotion features={domAnimation}>{children}</LazyMotion>
    </MotionConfig>
  );
}

export function PageTransition({ pageKey, children }: { pageKey: string; children: ReactNode }) {
  return (
    <AnimatePresence mode="wait" initial={false}>
      <m.div
        key={pageKey}
        className="app-page-motion"
        initial={{ opacity: 0, y: 7 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.2, ease: MOTION_EASE }}
      >
        {children}
      </m.div>
    </AnimatePresence>
  );
}

export function ContentSwap({ swapKey, children, className = "" }: { swapKey: string; children: ReactNode; className?: string }) {
  return (
    <AnimatePresence mode="wait" initial={false}>
      <m.div key={swapKey} className={`motion-content-swap ${className}`} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18, ease: MOTION_EASE }}>
        {children}
      </m.div>
    </AnimatePresence>
  );
}

export { AnimatePresence, m };
