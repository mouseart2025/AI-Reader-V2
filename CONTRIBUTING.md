# 贡献指南 Contributing Guide

[中文](#) | [English](./CONTRIBUTING.en.md) *(coming soon)*

感谢你对 AI Reader V2 的关注！无论是报告 Bug、建议功能、改进文档还是提交代码，我们都欢迎你的参与。

---

## 参与方式 Ways to Contribute

- **报告 Bug** — 通过 [Issue 模板](https://github.com/mouseart2025/AI-Reader-V2/issues/new/choose) 提交
- **功能建议** — 通过 [Feature Request](https://github.com/mouseart2025/AI-Reader-V2/issues/new/choose) 提交
- **改进文档** — 修正错别字、补充说明、改善翻译
- **提交代码** — 修复 Bug 或实现新功能（请先开 Issue 讨论）
- **测试反馈** — 上传不同类型的小说，报告分析质量问题

---

## 开发环境搭建 Development Setup

### 环境要求 Prerequisites

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.9+ | 后端运行时 |
| Node.js | 22+ | 前端构建工具 |
| [uv](https://docs.astral.sh/uv/) | latest | Python 包管理器 |
| [Ollama](https://ollama.com/) | latest | 本地 LLM 推理（或配置云端 API） |

### 克隆仓库

```bash
git clone https://github.com/mouseart2025/AI-Reader-V2.git
cd AI-Reader-V2
```

### 后端 Backend

```bash
cd backend
uv sync                                          # 安装 Python 依赖
uv run uvicorn src.api.main:app --reload         # 启动开发服务器 (localhost:8000)
```

### 前端 Frontend

```bash
cd frontend
npm install                                      # 安装 Node 依赖
npm run dev                                      # 启动开发服务器 (localhost:5173)
```

前端开发服务器自动将 `/api` 和 `/ws` 请求代理到后端 `localhost:8000`。

### 验证 Verify Setup

1. 确保 Ollama 运行中（`ollama serve`），或配置云端 API（参见 `CLAUDE.md` 的环境变量部分）
2. 打开 http://localhost:5173
3. 上传一本 .txt 小说，启动分析，确认 LLM 连接正常

---

## 代码规范 Code Conventions

详细规范参见 [CLAUDE.md](./CLAUDE.md) 的 Code Conventions 部分。以下是要点：

### 后端 Python

- **文件命名**: `snake_case.py`
- **类名**: `PascalCase`（如 `AnalysisService`、`ChapterFactExtractor`）
- **函数**: `snake_case`（如 `get_chapter_facts()`）
- **常量**: `UPPER_SNAKE_CASE`
- **异步优先**: 所有 DB、HTTP、LLM 调用使用 `async/await`
- **错误消息**: 中文
- **无 ORM**: 直接 SQL + Pydantic 模型

### 前端 TypeScript / React

- **组件**: `PascalCase.tsx`（如 `BookshelfPage.tsx`）
- **Store**: `camelCase` + `Store.ts`（如 `novelStore.ts`）
- **Hooks**: `use` 前缀 + `camelCase.ts`（如 `useEntity.ts`）
- **类型**: `PascalCase`，不使用 `I` 前缀
- **路径别名**: `@/` 映射到 `src/`
- **严格模式**: `strict: true`，`noUnusedLocals`，`noUnusedParameters`

### API 路由

- 路由：小写复数名词，kebab-case（如 `/api/novels/{id}/chapter-facts`）
- 查询参数：`snake_case`（如 `?chapter_start=1&chapter_end=50`）

---

## Commit Message 规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式，描述使用中文：

```
<type>: <中文描述>
```

### 类型 Types

| 类型 | 用途 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: 添加书签导出功能` |
| `fix` | Bug 修复 | `fix: 修复地图标签重叠问题` |
| `docs` | 文档更新 | `docs: 更新 README 功能列表` |
| `chore` | 维护/构建 | `chore: 更新依赖版本` |
| `refactor` | 重构 | `refactor: 简化实体聚合逻辑` |
| `style` | 格式/样式 | `style: 统一缩进格式` |
| `test` | 测试 | `test: 添加别名解析单元测试` |

可选 scope：`feat(map): 添加轨迹动画暂停功能`

---

## Pull Request 流程

### 1. Fork 仓库

点击 GitHub 页面右上角的 **Fork** 按钮。

### 2. 创建分支

```bash
git checkout -b feat/your-feature-name
# 或
git checkout -b fix/issue-description
```

分支命名：`feat/xxx`、`fix/xxx`、`docs/xxx`、`refactor/xxx`

### 3. 编写代码并提交

```bash
git add <changed-files>
git commit -s -m "feat: 你的功能描述"
```

> `-s` 标志会自动添加 `Signed-off-by` 行（参见下方 DCO 说明）。

### 4. 推送并创建 PR

```bash
git push origin feat/your-feature-name
```

在 GitHub 上创建 Pull Request，填写 PR 模板。

### PR 提交前检查清单

- [ ] `npm run build` 通过（前端 TypeScript 类型检查 + 构建）
- [ ] `npm run lint` 通过（前端 ESLint）
- [ ] 后端正常启动无报错
- [ ] 已用至少一本小说测试过变更
- [ ] 新增/修改的 API 端点已在代码中记录
- [ ] Commits 已使用 `-s` 签名

---

## 许可证 License

### AGPL v3

本项目使用 [GNU Affero General Public License v3.0](./LICENSE)。提交代码即表示你同意以 AGPL v3 许可证发布你的贡献。

### Developer Certificate of Origin (DCO)

本项目使用 DCO 签名流程。通过在 commit 中添加 `Signed-off-by` 行，你证明你有权提交该代码，并同意以项目的开源许可证发布。

签署 commit：

```bash
git commit -s -m "feat: 你的功能描述"
```

这会自动在 commit message 末尾添加：

```
Signed-off-by: Your Name <your.email@example.com>
```

> 如果忘记签名，可以追加：`git commit --amend -s`

### 商业许可

本项目提供双轨许可。开源使用遵循 AGPL v3，商业私有部署可购买商业许可。详见 [LICENSE-COMMERCIAL.md](./LICENSE-COMMERCIAL.md)。你提交的代码将始终在 AGPL v3 下保持可用。

---

## 获得帮助 Getting Help

- **Bug / 功能建议**: [GitHub Issues](https://github.com/mouseart2025/AI-Reader-V2/issues)
- **问题讨论**: [GitHub Discussions](https://github.com/mouseart2025/AI-Reader-V2/discussions)
- **架构了解**: 阅读 [CLAUDE.md](./CLAUDE.md)
