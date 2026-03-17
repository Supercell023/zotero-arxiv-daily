# 系统优化说明

## 已完成的优化

### 1. 改进的 LLM 提示词

**新的 TLDR 生成策略：**
- ✅ 突出**关键创新**（这篇论文有什么新东西？）
- ✅ 说明**实际影响**（为什么重要？）
- ✅ 标注**意外发现**（有什么惊喜？）
- ✅ 保持简洁（2-3 句话）

**效果：**
- 更有价值的摘要，帮你快速判断是否值得深���
- 使用 deepseek-chat 生成，成本低速度快

---

### 2. "小惊喜"机制（Diversity Bonus）

**问题：** 纯粹基于相似度的推荐容易形成"回声室"，只推荐你已知领域的论文。

**解决方案：** 添加多样性奖励机制
- 对**中等相似度**（0.3-0.7）的论文给予小幅加分
- 这些论文既不太远（完全不相关），也不太近（你已经很熟悉）
- 帮你发现**相邻领域的有趣工作**

**配置：**
```yaml
reranker:
  diversity:
    enabled: true
    bonus_strength: 0.3  # 0.0-1.0，越高惊喜越多
```

**效果：**
- 在你的 50 篇推荐中，会有几篇"意外之喜"
- 不会破坏主要推荐质量（加分很小）
- 帮你拓展研究视野

---

### 3. Local Reranker 性能优化建议

**你的配置分析：**
```yaml
executor:
  max_workers: 16  # ✅ 很好，高并发
  max_paper_num: 50  # ✅ 合理数量
  reranker: local  # 使用本地模型

reranker:
  local:
    model: jinaai/jina-embeddings-v5-text-nano  # ✅ 轻量级模型
```

**性能优化建议：**

#### 选项 1：使用更快的 embedding 模型
```yaml
reranker:
  local:
    model: sentence-transformers/all-MiniLM-L6-v2  # 更快，质量也不错
```

#### 选项 2：缓存 Zotero 库的 embeddings
在 GitHub Action 中缓存你的文献库 embeddings，避免每次重新计算：

```yaml
# .github/workflows/main.yml
- name: Cache embeddings
  uses: actions/cache@v3
  with:
    path: ~/.cache/zotero_embeddings
    key: zotero-embeddings-${{ hashFiles('**/zotero_corpus.json') }}
```

#### 选项 3：减少 LLM 调用（最大优化）
```yaml
executor:
  max_workers: 32  # 进一步提高并发

llm:
  generation_kwargs:
    model: deepseek-chat
    max_tokens: 200  # 限制 TLDR 长度，加快生成
```

---

### 4. 针对你配置的优化建议

**当前配置：**
```yaml
source:
  arxiv:
    category: ["cs.AI","cs.CV","cs.LG","cs.CL","cs.RO","cs.SY","cs.CE"]
    include_cross_list: true
```

**问题：** 7 个类别 + cross-list，每天可能有 200-300 篇新论文

**优化建议：**

#### 方案 A：分阶段过滤（推荐）
```yaml
executor:
  max_paper_num: 50  # 最终推荐 50 篇
  pre_filter_num: 150  # 先用 embedding 筛选出 150 篇

reranker:
  diversity:
    enabled: true
    bonus_strength: 0.4  # 稍微提高惊喜度
```

#### 方案 B：聚焦核心类别
```yaml
source:
  arxiv:
    category: ["cs.AI","cs.CV","cs.LG","cs.CL"]  # 减少到 4 个核心类别
    include_cross_list: true
```

#### 方案 C：使用 Zotero 集合过滤
```yaml
zotero:
  include_path: "Research/Core/**"  # 只基于核心研究论文推荐
```

---

## 推荐的完整配置

```yaml
zotero:
  user_id: ${oc.env:ZOTERO_ID}
  api_key: ${oc.env:ZOTERO_KEY}
  include_path: null  # 或设置为你的核心集合

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
    max_tokens: 200  # 限制长度加快速度
  language: English

reranker:
  local:
    model: jinaai/jina-embeddings-v5-text-nano
    encode_kwargs:
      task: retrieval
      prompt_name: document
  diversity:
    enabled: true
    bonus_strength: 0.3  # 调整 0.2-0.5 之间
  tag_weights:
    ⭐⭐⭐⭐⭐: 5.0
    ⭐⭐⭐⭐: 4.0
    ⭐⭐⭐: 3.0

source:
  arxiv:
    category: ["cs.AI","cs.CV","cs.LG","cs.CL","cs.RO","cs.SY","cs.CE"]
    include_cross_list: true

executor:
  send_empty: false
  max_workers: 16  # 或 32
  max_paper_num: 50
  reranker: local
  source: ['arxiv']
```

---

## 使用建议

### 第一周：建立基线
1. 给 20-30 篇核心论文打 5 星 ⭐⭐⭐⭐⭐
2. 观察推荐质量
3. 记录哪些推荐很好，哪些不相关

### 第二周：调整参数
1. 如果推荐太保守（都是你熟悉的）：
   - 提高 `bonus_strength` 到 0.4-0.5

2. 如果推荐太发散（不相关的多）：
   - 降低 `bonus_strength` 到 0.1-0.2
   - 或设置 `enabled: false` 关闭多样性

3. 如果速度太慢：
   - 减少 `max_paper_num` 到 30
   - 或减少 arxiv 类别

### 持续优化
- 每周给新添加的论文打星级
- 调整 `diversity.bonus_strength` 找到最佳平衡
- 在 `feedback.yaml` 中记录特别好/差的推荐

---

## 预期效果

**推荐的 50 篇论文中：**
- 35-40 篇：高度相关（基于你的 5 星论文）
- 8-12 篇：中等相关但有趣（多样性奖励）
- 2-3 篇：意外之喜（跨领域但可能有启发）

**TLDR 质量：**
- 快速了解论文核心创新
- 判断是否值得深读
- 发现意外的有趣点

---

## 故障排查

**推荐质量不好？**
1. 检查是否给足够多的论文打了星级（至少 20 篇）
2. 确认星级分布合理（不要全是 5 星）
3. 查看日志中的 "avg weight" 是否合理

**速度太慢？**
1. 减少 `max_paper_num`
2. 提高 `max_workers`
3. 考虑减少 arxiv 类别

**没有惊喜？**
1. 提高 `diversity.bonus_strength`
2. 检查是否 Zotero 库太小（需要至少 50 篇论文）

---

开始使用吧！系统会持续学习你的兴趣，并偶尔给你带来惊喜 ✨
