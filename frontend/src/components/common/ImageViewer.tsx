import { CornersIn, MagnifyingGlassPlus } from "@phosphor-icons/react";
import { useMemo, useRef, useState } from "react";
import Lightbox, { type ZoomRef } from "yet-another-react-lightbox";
import Download from "yet-another-react-lightbox/plugins/download";
import Fullscreen from "yet-another-react-lightbox/plugins/fullscreen";
import Zoom from "yet-another-react-lightbox/plugins/zoom";
import "yet-another-react-lightbox/styles.css";

export interface ImageViewerProps {
  open: boolean;
  src: string;
  alt: string;
  filename: string;
  title: string;
  onClose: () => void;
  onDownloadError?: (message: string) => void;
}

export interface ImagePreviewTriggerProps {
  src: string;
  alt: string;
  imageClassName: string;
  onOpen: () => void;
  onError?: () => void;
  label?: string;
  className?: string;
}

export function ImagePreviewTrigger({
  src,
  alt,
  imageClassName,
  onOpen,
  onError,
  label = "查看大图",
  className = "",
}: ImagePreviewTriggerProps) {
  const [loadedSrc, setLoadedSrc] = useState("");
  const ready = loadedSrc === src;

  return (
    <button
      type="button"
      className={`image-preview-trigger ${className}`.trim()}
      aria-label={`${label}：${alt}`}
      title={label}
      disabled={!ready}
      onClick={onOpen}
    >
      <img
        className={imageClassName}
        src={src}
        alt={alt}
        onLoad={() => setLoadedSrc(src)}
        onError={() => {
          setLoadedSrc("");
          onError?.();
        }}
      />
      <span className="image-preview-trigger-hint" aria-hidden="true">
        <MagnifyingGlassPlus size={18} />
        {label}
      </span>
    </button>
  );
}

export function ImageViewer({
  open,
  src,
  alt,
  filename,
  title,
  onClose,
  onDownloadError,
}: ImageViewerProps) {
  const zoomRef = useRef<ZoomRef | null>(null);
  const slides = useMemo(
    () => (src ? [{ src, alt, download: { url: src, filename } }] : []),
    [alt, filename, src],
  );

  const resetButton = (
    <button
      key="fit-window"
      type="button"
      className="yarl__button image-viewer-fit-button"
      aria-label="适应窗口"
      title="适应窗口"
      onClick={() => zoomRef.current?.changeZoom(zoomRef.current.minZoom, false, 0, 0)}
    >
      <CornersIn size={26} weight="regular" aria-hidden="true" />
    </button>
  );

  const titleNode = (
    <div className="image-viewer-title" key="image-title" title={`${title} · ${filename}`}>
      <strong>{title}</strong>
      <span>{filename}</span>
    </div>
  );

  return (
    <Lightbox
      key={src}
      className="groupbrief-image-viewer"
      open={open && Boolean(src)}
      close={onClose}
      slides={slides}
      plugins={[Zoom, Fullscreen, Download]}
      carousel={{ finite: true, imageFit: "contain", padding: "24px" }}
      controller={{ aria: true, closeOnBackdropClick: true, disableSwipeNavigation: true }}
      zoom={{ ref: zoomRef, scrollToZoom: true, zoomInMultiplier: 1.5 }}
      render={{ buttonPrev: () => null, buttonNext: () => null }}
      toolbar={{ buttons: [titleNode, "zoom", resetButton, "fullscreen", "download", "close"] }}
      labels={{
        Lightbox: title,
        Slide: "图片",
        "Photo gallery": "图片查看器",
        "{index} of {total}": "第 {index} 张，共 {total} 张",
        Close: "关闭",
        Download: "另存原图",
        "Zoom in": "放大",
        "Zoom out": "缩小",
        "Enter Fullscreen": "进入全屏",
        "Exit Fullscreen": "退出全屏",
      }}
      download={{
        download: async ({ saveAs }) => {
          try {
            const response = await fetch(src);
            if (!response.ok) {
              const detail = await response.text();
              throw new Error(detail || `HTTP ${response.status}`);
            }
            const blob = await response.blob();
            if (!blob.size) throw new Error("图片文件为空");
            saveAs(blob, filename);
          } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            onDownloadError?.(`原图另存失败：${detail}`);
          }
        },
      }}
    />
  );
}
