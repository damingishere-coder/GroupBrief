# 参与 GroupBrief

感谢你愿意改进 GroupBrief。这个项目会处理本地聊天记录，请优先保护隐私，并让每个改动保持清晰、可验证。

## 开始前

- Bug 或小型文档修正可以直接提交 Pull Request。
- 新功能、数据库变化或发送策略变化请先创建 Issue 说明场景、边界和风险。
- 不要在 Issue、日志、截图、测试夹具或提交历史中放入真实聊天、群 ID、微信号、邮箱、Token、Cookie、API Key 或本机绝对路径。

## 本地开发

环境要求：Windows 10/11、Python 3.10+、Node.js 18+。

```powershell
git clone https://github.com/damingishere-coder/GroupBrief.git
Set-Location GroupBrief
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location frontend
npm ci
npm run build
Set-Location ..
```

启动：

```powershell
.\start_windows.bat
```

## 提交前验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
Set-Location frontend
npm run build
Set-Location ..
docker compose config --quiet
git diff --check
```

测试应使用 fake provider、Mock 数据或匿名化夹具，不得触发真实微信发送、真实邮件、真实生图或其他对外副作用。

## Pull Request 原则

- 一个 PR 只解决一个主题。
- 说明用户可见变化、验证命令和结果。
- 对外部环境未验证的能力明确写出限制，不用测试结果替代真实端到端验收。
- 不重写他人的提交历史，不提交运行产物、数据库、日志、`.env` 或开发过程备份。
