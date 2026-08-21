# MAOP 5 分钟快速入门

> 本文档帮助你从零开始，在 5 分钟内启动 MAOP 并运行第一个 Agent 任务。

## 前置要求

| 组件 | 最低版本 | 检查命令 |
|------|---------|----------|
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| Git | 任意 | `git --version` |

## 1. 安装（1 分钟）

```bash
# 克隆仓库
git clone <MAOP_REPO_URL>
cd MAOP

# 安装 Python 后端依赖
pip install -e py/

# 安装前端依赖
cd dashboard-enterprise
npm install
cd ..
```

## 2. 配置（1 分钟）

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，至少设置以下变量：
#   MAOP_LLM_API_KEY=<你的 API Key>    （必填）
#   MAOP_LLM_API_BASE=<API 地址>      （可选，默认 OpenAI）
#   MAOP_LLM_MODEL=gpt-4o             （可选）
#   MAOP_DASH_PORT=9079               （可选，默认 9079）
```

> 📸 截图：`.env` 文件关键配置区域

## 3. 启动（1 分钟）

打开两个终端窗口：

**终端 1 — 启动后端：**
```bash
cd py
python -m maop.dashboard.server
```
看到 `Uvicorn running on http://0.0.0.0:9079` 表示启动成功。

**终端 2 — 启动前端：**
```bash
cd dashboard-enterprise
npm run dev
```
看到 `Local: http://localhost:5173` 表示启动成功。

> 📸 截图：两个终端窗口分别显示后端和前端的启动日志

## 4. 运行第一个 Agent 任务（2 分钟）

### 4.1 打开 Dashboard
浏览器访问 `http://localhost:5173`

> 📸 截图：Dashboard 首页（首次访问会显示新手引导）

### 4.2 创建 Agent
1. 点击左侧导航栏 **Agents**
2. 点击 **新建 Agent** 按钮
3. 填写信息：
   - **名称**：`我的第一个 Agent`
   - **系统提示词**：`你是一个有用的助手，用简洁的中文回答问题。`
   - **模型**：选择你配置的 LLM 模型
4. 点击 **保存**

> 📸 截图：Agent 创建表单

### 4.3 运行任务
1. 在 Agent 列表中点击刚创建的 Agent
2. 进入 **Chat** 面板
3. 输入一个任务，例如：`用 Python 写一个冒泡排序函数`
4. 按 Enter 发送
5. 观察 Agent 的 Plan→Execute→Verify 执行过程

> 📸 截图：Agent 执行结果的 Chat 界面

## 5. 下一步

- 📖 [自演化使用指南](evolution-guide.md) — 了解 Agent 如何自我优化
- 🔧 [部署指南](deployment.md) — 生产环境部署
- 🏗 [架构设计文档](adr/) — 深入理解 MAOP 架构
- 📋 [变更日志](../CHANGELOG.md) — 了解最新功能

## FAQ

### Q: 启动后端时提示 `ModuleNotFoundError: No module named 'maop'`
**A:** 确保已运行 `pip install -e py/`（editable 模式安装）。如果仍有问题，检查 Python 版本是否 ≥ 3.11。

### Q: 前端页面空白或 API 请求失败
**A:** 检查后端是否正在运行（默认端口 9079），确认 `.env` 中 `MAOP_DASH_PORT` 与前端代理配置一致。

### Q: Agent 执行时报 LLM API 错误
**A:** 检查 `.env` 中 `MAOP_LLM_API_KEY` 是否正确设置，以及 API 地址是否可访问。可以用 `curl` 测试 API 连通性。

### Q: 企业版功能（SSO/RBAC/审计）显示 404
**A:** Community 版默认启用 `personal` edition。企业版功能需要在 `.env` 中设置 `MAOP_EDITION=enterprise` 并配置有效 License。详见 [ADR-016](adr/016-dual-edition-architecture.md)。

---

需要更多帮助？查看完整文档：[📚 文档中心](README.md)