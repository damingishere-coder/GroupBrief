import { useCallback, useEffect, useState } from "react";

import {
  getImagePromptTemplate,
  GroupV2,
  ImageThemeOption,
  listGroups,
  listImageThemes,
} from "../../../api";
import { describeLoadError } from "./model";

export type ToastFn = (message: string) => void;

export function useAIImageCatalogs(toast: ToastFn) {
  const [groups, setGroups] = useState<GroupV2[]>([]);
  const [themes, setThemes] = useState<ImageThemeOption[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [groupsError, setGroupsError] = useState("");
  const [themesError, setThemesError] = useState("");
  const [globalDefaultPrompt, setGlobalDefaultPrompt] = useState("");
  const [defaultTemplateError, setDefaultTemplateError] = useState("");

  const loadCatalogs = useCallback(async () => {
    setCatalogLoading(true);
    setGroupsError("");
    setThemesError("");
    setDefaultTemplateError("");
    const [groupResult, themeResult, promptResult] = await Promise.allSettled([
      listGroups(),
      listImageThemes(),
      getImagePromptTemplate("default"),
    ]);
    if (groupResult.status === "fulfilled") {
      setGroups(groupResult.value);
    } else {
      const message = describeLoadError("群配置", groupResult.reason);
      setGroupsError(message);
      toast(message);
    }
    if (themeResult.status === "fulfilled") {
      setThemes(themeResult.value.themes);
    } else {
      const message = describeLoadError("主题目录", themeResult.reason);
      setThemesError(message);
      toast(message);
    }
    if (promptResult.status === "fulfilled") {
      setGlobalDefaultPrompt(promptResult.value.content);
    } else {
      const message = describeLoadError("默认 Prompt", promptResult.reason);
      setDefaultTemplateError(message);
      toast(message);
    }
    setCatalogLoading(false);
  }, [toast]);

  useEffect(() => {
    void loadCatalogs();
  }, [loadCatalogs]);

  return {
    groups,
    themes,
    catalogLoading,
    groupsError,
    themesError,
    globalDefaultPrompt,
    defaultTemplateError,
    loadCatalogs,
  };
}
