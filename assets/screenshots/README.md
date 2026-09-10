# 公开截图

2.0 首页使用 `dashboard.png`、`report-workspace.png` 和 `group-management.png`。这些图片由真实前端与 Playwright 拦截的匿名演示 API 渲染；不读取生产数据库，不调用生成或发送接口。旧的 `ranking.png`、`archive.png` 保留供历史记录追溯，不代表 2.0 导航。

在 `frontend` 目录使用 PowerShell 更新：

```powershell
npm run build
$env:GROUPBRIEF_CAPTURE_DOCS = "1"
$env:GROUPBRIEF_E2E_PORT = "14174"
npx playwright test dashboard.spec.ts groups-list.spec.ts --grep "capture public|1280px"
Remove-Item Env:GROUPBRIEF_CAPTURE_DOCS
Remove-Item Env:GROUPBRIEF_E2E_PORT
```

开始前确保选定端口没有其他服务。更新后人工检查图片完整性、页面加载状态与匿名数据，再提交。常规 CI 跳过截图写入步骤。

首页布局参考 [Dify](https://github.com/langgenius/dify)、[Open WebUI](https://github.com/open-webui/open-webui) 和 [LobeHub](https://github.com/lobehub/lobehub) 的产品介绍、视觉预览、快速开始与文档入口组织方式；品牌横幅为本项目自制 SVG，截图来自本项目代码。
