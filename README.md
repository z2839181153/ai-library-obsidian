# AI 图书馆（ai-library-obsidian）

用 AI + Obsidian 搭建"图书馆式"个人知识库：AI 是图书管理员/采购员/档案员。

- **入馆**：多格式素材（PDF/EPUB/网页/公众号/聊天记录/视频转写）→ 清洗 → 不可变副本
- **编目分类**：楼层（来源媒介）→ 房间（语义主题）→ 书架 → 书（可自定义）
- **蒸馏技能**：把方法论类书蒸馏成可执行 SKILL.md（人工审阅）
- **检索问答**：混合检索（FTS5 词法 + LanceDB 向量）+ 技能路由 + `[[wikilink]]` 引用
- **每日采购**：无订阅清单，方向 = 历史提问 + 热门源；配额按藏书比重由 AI 动态计算
- **主人主权**：所有 AI 操作是建议，主人确认才生效

## 形态

形态 C：独立 Python 后端（FastAPI，`127.0.0.1:8800`）+ Obsidian 联动（薄插件二期）+ Agent Skills 内核 + MCP 接口。

## 快速开始

```powershell
# Windows
.\start.ps1

# WSL / Linux
./start.sh
```

首次运行自动创建 `.venv` 并安装依赖。启动后浏览器打开 http://127.0.0.1:8800

## 文档

- `详细设计文档.md` — 设计蓝图（架构/数据模型/流程/UI/隐私/MVP P0-P4）
- `交接文档.md` — 项目交接（新会话第一份要读的文件）

## 目录

```
app/         后端包（FastAPI）
config/      配置文件（settings.json）
tests/       pytest 测试
web/         前端（Vue3，P3 阶段）
data/        运行时数据（SQLite/LanceDB，不入库）
```

## 路线图

P0 索引管线 → P1 编目闭环 → P2 蒸馏闭环 → P3 Web UI MVP → P4 打磨
