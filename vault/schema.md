# Vault Schema（图书馆目录结构规范）

> AI 图书馆的 vault 采用「楼层 → 房间 → 书架 → 书」四级目录组织。
> 本文件描述目录结构与元数据格式，供开发与排障参考。实现见 `app/core/shelving.py`、`app/db/schema.py`。

## 顶层目录

```
vault/
├─ books/       正式藏书（唯一长期落盘的层级目录）
├─ catalog/     图书卡片 bk_<id>.md（可选，生成分类建议后出现）
├─ skills/      蒸馏技能（可选，蒸馏批准后出现）
├─ pending/     采购候选（可选）
├─ archive/     档案馆：原始副本/蒸馏日志/备份（原始副本实际在 data/archive/raw）
├─ README.md    本导览
└─ schema.md    本文件
```

> 注意：原始不可变副本实际存放于 `data/archive/raw/<h2>/<hash>`（内容寻址，无扩展名），
> `vault/archive/` 仅存蒸馏过程日志等辅助产物。

## books/ 层级

```
books/<楼层>/<房间>/<书架>/<书名>/book.md
```

| 层级 | 元数据文件 | 说明 |
|---|---|---|
| 楼层 | `.floor.json` | 来源媒介（默认 1F 电子书 / 2F 网页公众号 / 3F 聊天记录 / 4F 视频转写），可增删改名 |
| 房间 | `.room.json` | 语义主题（如 机器学习、文学），可增删改名 |
| 书架 | `.shelf.json` | 主题细分（如 入门），可增删改名 |
| 书 | `book.md` | 上架书的正文（清洗后的不可变副本），目录名 = 书名 |

### 元数据文件格式

`.floor.json`：

```json
{
  "floor_id": "fl_1f_ebook",
  "name": "电子书",
  "code": "1F",
  "media_type": "pdf",
  "description": "PDF/EPUB 电子书",
  "ord": 1
}
```

`.room.json`：

```json
{
  "room_id": "rm_<hash>",
  "floor_id": "fl_1f_ebook",
  "name": "机器学习",
  "description": ""
}
```

`.shelf.json`：

```json
{
  "shelf_id": "sh_<hash>",
  "room_id": "rm_<hash>",
  "name": "入门",
  "description": ""
}
```

### 命名规则

- 楼层目录：`<code>-<名称>`（如 `1F-电子书`；名称改动后目录会重建映射，`code` 不变）
- 房间/书架目录：直接使用名称（如 `机器学习/入门`）
- 书目录：书名（同名冲突时追加区分）

### 规则

- 楼层 = 来源媒介、房间 = 语义主题，层级依次下降（楼层 → 房间 → 书架 → 书），全部可增删改名
- **有书的楼层/房间/书架禁止删除**（后端 409 拒绝，须先移走书）
- 虚拟书架：按标签自动聚合（不占磁盘目录，见 Web UI 标签筛选）

## catalog/ 图书卡片

文件名：`catalog/bk_<id>.md`（`<id>` 为数据库 book id）。

内容：书名 / 一句话简介 / 摘要 / 章节 / 关键概念 / 标签 / 蒸馏价值评估。
由 `app/core/card_generator.py` 生成（LLM），主人可编辑；`book.md` 内的 `[[catalog/bk_<id>]]` 引用可在 Obsidian 中点击跳转。

## skills/ 蒸馏技能

文件名：`skills/<slug>/SKILL.md`（cangjie 规范：frontmatter + R/I/A1/A2/E/B 六段 + `test-prompts.json`）。
审批流：draft → reviewing → approved → installed（人审确认后安装）；拒绝 5 次自动 blocked。

## 数据归属

- **知识内容**（书/卡片/技能）→ vault 目录，与 Obsidian 直接共享
- **软件数据**（SQLite 索引/向量库/密钥/原始副本）→ `data/` 目录（不在 vault 内）
- vault 默认被 `.gitignore` 排除（公开仓库不含个人知识库内容），本文件与 `README.md` 为白名单例外
