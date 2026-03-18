# 配置分析和优化建议

## 你当前的配置分析

```yaml
executor:
  max_paper_num: 50
  first_stage_reranker: llm_fast  # ✅ 好
  reranker: llm  # ⚠️ 问题：没有设置 pre_filter_num

reranker:
  llm:
    max_corpus_samples: 20
    batch_size: 5
  # ❌ 缺少 llm_fast 配置
```

---

## 问题分析

### 问题 1：缺少 `pre_filter_num`
```yaml
executor:
  first_stage_reranker: llm_fast  # 设置了第一阶段
  # ❌ 但没有 pre_filter_num，所以两阶段不会启动
```

**结果：** 系统会直接用 `llm` reranker 处理所有 1200 篇论文，非常慢（40-60 分钟）。

### 问题 2：缺少 `llm_fast` 配置
```yaml
reranker:
  llm:  # 有这个
    max_corpus_samples: 20
  # ❌ 缺少 llm_fast 配置
```

### 问题 3：`max_tokens` 太小
```yaml
llm:
  generation_kwargs:
    max_tokens: 1024  # ⚠️ 可能不够
```

对于双语 TLDR（英文 + 中文），1024 可能不够。

---

## 最优配置（基于你的需求）

### 方案 A：两阶段 LLM（推荐）

```yaml
zotero:
  user_id: ${oc.env:ZOTERO_ID}
  api_key: ${oc.env:ZOTERO_KEY}
  include_path: null

email:
  sender: ${oc.env:SENDER}
  receiver: ${oc.env:RECEIVER}
  smtp_server: smtp.qq.com
  smtp_port: 465
  sender_password: ${oc.env:SENDER_PASSWORD}

llm:
  api:
    key: ${oc.env:OPENAI_API_KEY}
    base_url: ${oc.env:OPENAI_API_BASE}
  generation_kwargs:
    model: deepseek-chat
    max_tokens: 1536  # 增加到 1536
  language: English

reranker:
  llm_fast:  # 添加这个
    max_corpus_samples: 10  # 第一阶段快速筛选
    batch_size: 20  # 大批量
  llm:
    max_corpus_samples: 20  # 第二阶段精排
    batch_size: 5
  tag_weights:
    ⭐⭐⭐⭐⭐: 2.5
    ⭐⭐⭐⭐: 2.3
    ⭐⭐⭐: 2.0
    ⭐⭐: 1.5
    ⭐: 1.2
  diversity:
    enabled: true
    bonus_strength: 0.3

source:
  arxiv:
    category: ["cs.AI","cs.CV","cs.LG","cs.CL","cs.RO","cs.SY","cs.CE"]
    include_cross_list: true

executor:
  send_empty: false
  max_workers: 16
  max_paper_num: 50
  pre_filter_num: 200  # 关键：添加这个
  first_stage_reranker: llm_fast
  reranker: llm
  debug: ${oc.env:DEBUG,null}
  source: ['arxiv']
```

**流程：**
```
1200 篇论文
  ↓ LLM Fast (5-8 分钟)
200 篇候选
  ↓ 提取 PDF (10-15 分钟)
200 篇带 PDF
  ↓ LLM 精排 (8-12 分钟)
50 篇最终推荐
  ↓ 生成 TLDR (5-10 分钟)
发送邮件
```

**总时间：** 30-45 分钟
**Token 消耗：** ~400k tokens/天
**成本：** ~$0.06-0.10/天

---

### 方案 B：混合模式（平衡）

```yaml
executor:
  pre_filter_num: 200
  first_stage_reranker: llm_fast  # LLM 快速筛选
  reranker: local  # Embedding 精排（更快）

reranker:
  llm_fast:
    max_corpus_samples: 10
    batch_size: 20
  local:
    model: jinaai/jina-embeddings-v5-text-nano
  # ... 其他配置相同
```

**总时间：** 25-35 分钟
**Token 消耗：** ~300k tokens/天
**成本：** ~$0.05/天

---

### 方案 C：纯 Embedding（最快）

```yaml
executor:
  pre_filter_num: 200
  first_stage_reranker: local  # Embedding 快速筛选
  reranker: local  # Embedding 精排

reranker:
  local:
    model: jinaai/jina-embeddings-v5-text-nano
  # ... 其他配置相同
```

**总时间：** 20-30 分钟
**Token 消耗：** ~100k tokens/天（只有 TLDR）
**成本：** ~$0.02/天

---

## 对比表

| 方案 | 第一阶段 | 第二阶段 | 时间 | Token | 成本 | 质量 |
|------|----------|----------|------|-------|------|------|
| **A. 两阶段LLM** | LLM Fast | LLM | 30-45分钟 | 400k | $0.08 | 最好 |
| **B. 混合模式** | LLM Fast | Embedding | 25-35分钟 | 300k | $0.05 | 很好 |
| **C. 纯Embedding** | Embedding | Embedding | 20-30分钟 | 100k | $0.02 | 良好 |

---

## 我的推荐

### 如果你追求最高质量 → 方案 A
- 两阶段都用 LLM
- 推荐最准确
- 成本可接受（$0.08/天）

### 如果你追求平衡 → 方案 B（推荐）
- 第一阶段 LLM 快速筛选（保证质量）
- 第二阶段 Embedding 精排（保证速度）
- 性价比最高

### 如果你追求速度 → 方案 C
- 全程 Embedding
- 最快最便宜
- 质量也不错

---

## 立即可用的配置

我建议你用**方案 B（混合模式）**：

```yaml
executor:
  pre_filter_num: 200  # 添加这个
  first_stage_reranker: llm_fast
  reranker: local  # 改成 local

reranker:
  llm_fast:  # 添加这个
    max_corpus_samples: 10
    batch_size: 20
  local:  # 添加这个
    model: jinaai/jina-embeddings-v5-text-nano
    encode_kwargs:
      task: retrieval
      prompt_name: document

llm:
  generation_kwargs:
    max_tokens: 1536  # 增加到 1536
```

---

需要我帮你生成完整的配置文件吗？
