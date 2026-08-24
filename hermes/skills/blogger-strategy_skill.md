---
name: blogger-strategy
description: 微博大 V 投资策略提炼：读取指定博主的博文 → 调 LLM 总结其投资方法论/风格/偏好
---

# 微博大 V 投资策略提炼

工作目录即 stock-watch 仓库根目录。

## 触发方式

- **定时**：注册为 Hermes cron（如每周五 20:00 自动跑）
- **自然语言**：在 Hermes 对话中直接说 "分析下最近微博大V的观点" 或 "帮我看看 XX 最近在聊什么"

## 前置

确保已跑过微博全量 + 增量,博文已入库(库路径见 `config.json` 的 `db_path`,默认 `data/app.db`;数据在 `posts` 表)。

## 流程

### 1. 读取博文数据

库路径见 `config.json` 的 `db_path`(默认 `data/app.db`)。`posts` 表关键列:

- `uid` — 博主 uid（对应 `config.json` 的 `bloggers[].uid`）
- `user_name` — 博主昵称
- `text` — 纯文本正文
- `created_ts` — 发布时间(epoch 秒)
- `reposts_count`, `comments_count`, `attitudes_count` — 互动量

### 2. 选分析模式

**单个博主**：
```sql
SELECT user_name, text, created_ts, reposts_count, comments_count
FROM posts WHERE uid = '<博主uid>'
ORDER BY created_ts DESC LIMIT 200;
```

**全部博主**（`config.json` 的 `bloggers` 清单）：
逐个 uid 查询,对每个博主独立分析。

### 3. 提炼投资策略

对每位博主,从博文中提取:

1. **核心风格** — 短线/中长线/价值/成长/题材/量化？一句话概括
2. **关注赛道** — 常提的行业/板块（如半导体、新能源、消费）
3. **典型手法** — 选股逻辑、买卖时机、仓位管理
4. **近期观点** — 最近 1-2 周在看什么、怎么看
5. **可信度信号** — 是否有实盘验证/逻辑一致性/事后复盘(不构成建议,仅观察记录)

**规则:**
- 只从博文原文中归纳,不编造
- 不搬运原文,只出加工结论
- 不评判对错,不推荐跟单
- 标注信息来源的时间跨度（如"基于近 200 条博文,时间跨度 2026.01–2026.08")
- 明确声明"仅供参考,不构成投资建议"

### 4. 输出格式

```
## 博主 A（基于 N 条博文,时间跨度 YYYY.MM–YYYY.MM）

- 风格: ...
- 赛道: ...
- 手法: ...
- 近期: ...
- 可信度: ...

## 博主 B
...
```

最终回复即是分析报告。
