import { Button, EmptyState, LoadingState, Toast } from "../../components/common";
import { useToast } from "../../components/ui";
import { AIImageRunWorkspace } from "./ai-images/AIImageRunWorkspace";
import { ImageStylePanel } from "./ai-images/ImageStylePanel";
import { useAIImageCatalogs } from "./ai-images/useAIImageCatalogs";
import { useAIImageRuns } from "./ai-images/useAIImageRuns";

export default function AIImages() {
  const { msg, toast } = useToast();
  const catalogs = useAIImageCatalogs(toast);
  const runModel = useAIImageRuns(catalogs.groups, toast);

  if (runModel.loading && !runModel.runs.length && !catalogs.groups.length) return <LoadingState label="正在加载 AI 图片工作台…" />;
  if (runModel.error && !runModel.runs.length) return <EmptyState title="AI 图片页面加载失败" description={runModel.error} action={<Button tone="secondary" onClick={runModel.loadRuns}>重新加载</Button>} />;

  return (
    <div className="ai-images-page">
      <ImageStylePanel {...catalogs} toast={toast} />
      <AIImageRunWorkspace
        model={runModel}
        themes={catalogs.themes}
        catalogLoading={catalogs.catalogLoading}
        themesError={catalogs.themesError}
        toast={toast}
      />
      <Toast message={msg} />
    </div>
  );
}
