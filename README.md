# 知识库治理检测工具

这是一个面向笔试任务的知识库治理小工具，用于自动扫描 FAQ/知识库条目，识别有问题的条目，给出治理建议，并输出可直接给业务方阅读的治理报告。

## 任务对应关系

本项目覆盖题目要求的 5 项交付：

1. **定义“问题条目”类型**
   - 以 `leixing.txt` 作为问题分类体系与严重度分级依据。
2. **实现检测工具**
   - `tool.py` 提供 CLI，可扫描知识库并输出结构化结果。
3. **给出治理建议**
   - 对每个问题输出建议动作（修改、合并、删除、新增、复核）。
4. **输出治理报告**
   - 支持 JSON 与 Markdown 两种格式。
5. **README**
   - 本文件说明分类体系、检测方法、治理逻辑与 AI 工具使用情况。

## 文件说明

- `tool.py`：主入口脚本
- `tool`：与 `tool.py` 同步的可执行脚本副本
- `task6_kb_articles.json`：知识库数据
- `task6_business_context.md`：业务基准规则文档
- `leixing.txt`：问题分类体系、严重度分级、样例说明

## 问题分类体系

本项目不在代码中手写定义问题类型，而是直接读取 `leixing.txt` 作为分类标准。

当前使用的 6 类问题：

- `rule_conflict`：规则冲突类
- `duplicate_conflict`：重复冲突类
- `empty_or_incomplete_answer`：空白/未完成类
- `incomplete_info`：信息不完整类
- `stale_risk`：时效高风险类
- `coverage_gap`：覆盖缺失类

严重度分级：

- `P0`：严重，优先修复
- `P1`：高优，优先补齐
- `P2`：中优，需要完善
- `P3`：风险，纳入复核

## 检测方法

本工具采用 **“条目级 + 库级” 双阶段 LLM 审核**，不再使用手写逐条业务规则判断。

### 1）条目级分析

输入：
- `leixing.txt`
- `task6_business_context.md`
- 一批 FAQ 条目

输出：
- 每条 FAQ 是否有问题
- 问题类型
- 严重程度
- 问题说明
- 影响
- 证据
- 治理建议

### 2）库级分析

输入：
- 全量知识库摘要
- 条目级分析结果
- 重复候选组
- `leixing.txt`
- `task6_business_context.md`

输出：
- 覆盖缺失
- 重复冲突组
- 跨条目口径不一致
- 治理优先级建议

### 3）为什么这样设计

单条 FAQ 适合判断：
- 规则冲突
- 空白答案
- 信息不完整
- 时效风险

全库视角更适合判断：
- 覆盖缺失
- 重复冲突组
- 主题级治理优先级

因此采用两阶段结构，而不是简单逐条扫描。

## 治理建议逻辑

工具会根据问题类型输出具体建议，典型动作包括：

- **修改**：条目存在错误，但主题仍然需要保留
- **合并**：同一问题有多条 FAQ，且答案冲突或重复
- **删除/下线**：旧条目明显错误，且不应继续保留
- **新增**：规则已存在，但知识库缺少对应标准 FAQ
- **复核**：条目未必错误，但属于高变动信息，需纳入周期治理

## AI 工具使用情况

本项目当前配置为 DashScope OpenAI-compatible 接口：

- `api_key=os.getenv("DASHSCOPE_API_KEY")`
- `base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"`
- `model="qwen3.7-plus"`
- `streaming=True`

说明：
- `llm` 模式下，工具通过 DashScope 调用 `qwen3.7-plus`。
- `mock` 模式下，不调用真实 API，而是返回一组内置的结构化样例结果，便于在无 Key 环境下演示完整流程。

## 运行方式

### 1）设置环境变量

Windows PowerShell：

```powershell
$env:DASHSCOPE_API_KEY="your_api_key"
```

### 2）运行 LLM 模式

输出 JSON：

```bash
python tool.py --mode llm --format json
```

输出 Markdown 报告：

```bash
python tool.py --mode llm --format markdown
```

### 3）运行 mock 模式

```bash
python tool.py --mode mock --format markdown
```

### 4）常用参数

```bash
python tool.py \
  --kb task6_kb_articles.json \
  --rules task6_business_context.md \
  --taxonomy leixing.txt \
  --mode llm \
  --model qwen3.7-plus \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --format json \
  --output report.json
```

可选参数：

- `--batch-size`：条目级批处理大小
- `--no-streaming`：关闭流式返回

## 输出结构

### JSON 输出

顶层字段包括：

- `mode`
- `provider`
- `model`
- `rules_file`
- `taxonomy_file`
- `kb_file`
- `total_articles`
- `summary`
- `results`
- `corpus_issues`
- `governance_priorities`

### Markdown 输出

报告包含：

1. 摘要结论
2. 问题类型分布
3. 严重程度分布
4. 高优先级治理建议
5. 条目问题明细
6. 库级问题

## 已知限制

- LLM 判断依赖 prompt 与上下文内容，结果稳定性低于纯规则程序，但泛化能力更强。
- 建议将 `P0` / `P1` 结果交由人工复核后再进入正式治理流程。
- 如果知识库规模显著增大，建议进一步拆批、压缩上下文或增加主题级审计流程。
