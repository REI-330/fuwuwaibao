# 06 人工裁定队列

生成时间：2026-09-30T15:14:48.179Z

> 本文件由 `knowledge/pipeline/06-adjudicate.mjs` 生成，**不要手改**。
> 人的输入只写进 `knowledge/review/adjudication.json`。

## 范围

- 全审（§4.2）：29 个节点 + 64 条边
  - 依据：occupation 节点，以及被 `requires` 指到的 skill 节点；边类型 requires / prerequisite / learning_unit / transitions_to
- 抽审（§4.2，≥20%）：从其余 87 条里确定性抽 22 条（FNV-1a 排序取前 N）
- 未编页/未审：其余 65 条保持 `llm-draft`

## 歧义 key（必须逐条裁定）

| key | 冲突 | 候选 | 机器提议 | 裁定 | 理由 |
|---|---|---|---|---|---|
| `堆内存属性与dma缓冲` | cross-kind | knowledge:AI001:stage3<br>skill:SK104 | `keep-separate` | _待填_ | _待填_ |
| `pytest` | cross-kind | skill:SK130<br>tool:pytest | `tool:pytest` | _待填_ | _待填_ |
| `selenium` | cross-kind | skill:SK132<br>tool:selenium | `tool:selenium` | _待填_ | _待填_ |

## 全审节点

| id | kind | label | 边数 |
|---|---|---|---|
| `occupation:AI001` | occupation | 嵌入式开发工程师 | 15 |
| `occupation:AI002` | occupation | 机器视觉工程师 | 11 |
| `occupation:AI003` | occupation | 自动化测试工程师 | 11 |
| `occupation:AI004` | occupation | 边缘 AI 工程师 | 14 |
| `skill:SK090` | skill | C 语言与内存模型 | 7 |
| `skill:SK101` | skill | 微控制器外设驱动 | 8 |
| `skill:SK102` | skill | SPI 总线事务设计 | 6 |
| `skill:SK103` | skill | GPIO 与引脚复用 | 4 |
| `skill:SK104` | skill | 堆内存属性与 DMA 缓冲 | 3 |
| `skill:SK105` | skill | 实时操作系统任务调度 | 5 |
| `skill:SK106` | skill | 中断与轮询传输取舍 | 4 |
| `skill:SK110` | skill | 目标检测模型训练 | 8 |
| `skill:SK111` | skill | 数据集格式与标注 | 3 |
| `skill:SK112` | skill | 模型验证与指标 | 4 |
| `skill:SK113` | skill | 模型导出与格式转换 | 7 |
| `skill:SK114` | skill | 实例分割 | 3 |
| `skill:SK115` | skill | 张量与自动微分基础 | 3 |
| `skill:SK120` | skill | 轻量级推理引擎集成 | 4 |
| `skill:SK121` | skill | 端侧推理性能基准测试 | 3 |
| `skill:SK122` | skill | 模型结构可视化检查 | 3 |
| `skill:SK123` | skill | 部署格式选型 | 6 |
| `skill:SK130` | skill | Python 测试框架 pytest | 5 |
| `skill:SK131` | skill | 夹具与测试隔离 | 5 |
| `skill:SK132` | skill | 浏览器自动化 WebDriver | 7 |
| `skill:SK133` | skill | 显式等待与隐式等待 | 5 |
| `skill:SK134` | skill | 端到端测试稳定性治理 | 4 |
| `skill:SK136` | skill | 分层夹具组织 conftest | 4 |
| `skill:SK137` | skill | WebDriver BiDi 双向协议 | 4 |
| `skill:SK215` | skill | 模型量化与部署 | 6 |

## 全审边

| 边 | 出处 |
|---|---|
| `requires` occupation:AI001 → skill:SK090 | S03#s46 |
| `requires` occupation:AI001 → skill:SK101 | S02#s7 |
| `requires` occupation:AI001 → skill:SK102 | S02#s10 |
| `requires` occupation:AI001 → skill:SK103 | S04#s74 |
| `requires` occupation:AI001 → skill:SK104 | S03#s53 |
| `requires` occupation:AI001 → skill:SK105 | S01#s2 |
| `requires` occupation:AI001 → skill:SK106 | S02#s11 |
| `requires` occupation:AI002 → skill:SK110 | S09#s154 |
| `requires` occupation:AI002 → skill:SK111 | S09#s155 |
| `requires` occupation:AI002 → skill:SK112 | S09#s156 |
| `requires` occupation:AI002 → skill:SK113 | S09#s159 |
| `requires` occupation:AI002 → skill:SK114 | S10#s162 |
| `requires` occupation:AI002 → skill:SK115 | S07#s124 |
| `requires` occupation:AI003 → skill:SK130 | S13#s202 |
| `requires` occupation:AI003 → skill:SK131 | S13#s204 |
| `requires` occupation:AI003 → skill:SK132 | S11#s172 |
| `requires` occupation:AI003 → skill:SK133 | S12#s181 |
| `requires` occupation:AI003 → skill:SK134 | S12#s180 |
| `requires` occupation:AI003 → skill:SK136 | S14#s256 |
| `requires` occupation:AI003 → skill:SK137 | S11#s176 |
| `requires` occupation:AI004 → skill:SK090 | S03#s46 |
| `requires` occupation:AI004 → skill:SK101 | S02#s7 |
| `requires` occupation:AI004 → skill:SK113 | S09#s159 |
| `requires` occupation:AI004 → skill:SK120 | S05#s98 |
| `requires` occupation:AI004 → skill:SK121 | S05#s100 |
| `requires` occupation:AI004 → skill:SK122 | S05#s97 |
| `requires` occupation:AI004 → skill:SK123 | S08#s146 |
| `requires` occupation:AI004 → skill:SK215 | S05#s96 |
| `prerequisite` skill:SK090 → skill:SK101 | S02#s7 |
| `prerequisite` skill:SK090 → skill:SK103 | S04#s74 |
| `prerequisite` skill:SK090 → skill:SK104 | S03#s53 |
| `prerequisite` skill:SK090 → skill:SK105 | S03#s46 |
| `prerequisite` skill:SK101 → skill:SK102 | S02#s10 |
| `prerequisite` skill:SK101 → skill:SK215 | S05#s96 |
| `prerequisite` skill:SK102 → skill:SK106 | S02#s12 |
| `prerequisite` skill:SK103 → skill:SK102 | S04#s73 |
| `prerequisite` skill:SK110 → skill:SK112 | S09#s156 |
| `prerequisite` skill:SK110 → skill:SK113 | S09#s159 |
| `prerequisite` skill:SK110 → skill:SK114 | S10#s164 |
| `prerequisite` skill:SK111 → skill:SK110 | S09#s155 |
| `prerequisite` skill:SK113 → skill:SK122 | S05#s97 |
| `prerequisite` skill:SK113 → skill:SK123 | S08#s146 |
| `prerequisite` skill:SK115 → skill:SK110 | S07#s124 |
| `prerequisite` skill:SK120 → skill:SK121 | S05#s100 |
| `prerequisite` skill:SK130 → skill:SK131 | S13#s204 |
| `prerequisite` skill:SK130 → skill:SK132 | S11#s172 |
| `prerequisite` skill:SK131 → skill:SK136 | S14#s256 |
| `prerequisite` skill:SK132 → skill:SK133 | S12#s181 |
| `prerequisite` skill:SK132 → skill:SK137 | S11#s176 |
| `prerequisite` skill:SK133 → skill:SK134 | S12#s180 |
| `prerequisite` skill:SK215 → skill:SK120 | S05#s98 |
| `prerequisite` skill:SK215 → skill:SK123 | S08#s145 |
| `learning_unit` occupation:AI001 → knowledge:AI001:stage1 | S03#s46 S04#s74 |
| `learning_unit` occupation:AI001 → knowledge:AI001:stage2 | S02#s10 S02#s17 |
| `learning_unit` occupation:AI001 → knowledge:AI001:stage3 | S03#s53 S03#s51 |
| `learning_unit` occupation:AI001 → knowledge:AI001:stage4 | S02#s23 |
| `learning_unit` occupation:AI002 → knowledge:AI002:stage1 | S09#s154 S09#s156 |
| `learning_unit` occupation:AI003 → knowledge:AI003:stage1 | S12#s180 S12#s187 |
| `learning_unit` occupation:AI003 → knowledge:AI003:stage2 | S14#s256 S13#s233 |
| `learning_unit` occupation:AI004 → knowledge:AI004:stage1 | S05#s96 S05#s101 |
| `learning_unit` occupation:AI004 → knowledge:AI004:stage2 | S05#s100 S05#s97 |
| `transitions_to` occupation:AI001 → occupation:AI002 | S09#s152 |
| `transitions_to` occupation:AI001 → occupation:AI004 | S08#s146 |
| `transitions_to` occupation:AI002 → occupation:AI004 | S05#s98 |

## 抽审样本

| 条目 | 类型 | 出处 |
|---|---|---|
| `belongs_to|skill:SK132|domain:test-automation` | edge | S11#s172 |
| `domain:embedded` | node · domain | S01#s2 S03#s47 |
| `knowledge:AI002:stage1` | node · knowledge | S09#s154 S09#s156 |
| `trains|task:T0060|skill:SK110` | edge | S09#s154 |
| `trains|task:T0060|skill:SK112` | edge | S09#s156 |
| `uses|occupation:AI003|tool:selenium` | edge | S11#s172 |
| `domain:edge-ai` | node · domain | S05#s94 S08#s146 |
| `belongs_to|skill:SK136|domain:test-automation` | edge | S14#s256 |
| `belongs_to|skill:SK134|domain:test-automation` | edge | S12#s180 |
| `trend:edge-ai-2026` | node · trend | S08#s145 S08#s149 |
| `tool:mindspore-lite` | node · tool | S05#s96 S05#s98 |
| `tool:paddlepaddle` | node · tool | S06#s113 S06#s112 |
| `belongs_to|skill:SK102|domain:embedded` | edge | S02#s10 |
| `belongs_to|skill:SK111|domain:vision` | edge | S09#s155 |
| `credential:osta-skill` | node · credential | S18#s343 |
| `knowledge:AI001:stage4` | node · knowledge | S02#s23 |
| `belongs_to|skill:SK130|domain:test-automation` | edge | S13#s202 |
| `knowledge:AI001:stage1` | node · knowledge | S03#s46 S04#s74 |
| `knowledge:AI001:stage2` | node · knowledge | S02#s10 S02#s17 |
| `domain:test-automation` | node · domain | S12#s180 S13#s202 |
| `knowledge:AI001:stage3` | node · knowledge | S03#s53 S03#s51 |
| `belongs_to|skill:SK104|domain:embedded` | edge | S03#s53 |

## 裁定结果（2026-09-15，human）

- 全审：accept —— 逐条核对到 chunk 原文：28 条 requires 的 importance/targetLevel 与出处段落一致；24 条 prerequisite 构成 DAG，无环且方向与原文「先…再…」表述一致；9 条 learning_unit 的 position 与该职业 S 段里的阶段顺序一致；3 条 transitions_to 的 horizon/deltaSkills 在原文里都有对应句子。未发现无原文支持的关系。
- 抽审：accept，已看 22 条 —— 22 条抽审样本（domain / knowledge / tool / credential / trend 节点，belongs_to / trains / uses 边）逐条回看了 sourceRefs 指向的 chunk，关系类型与原文表述一致，未出现「原文只提了一个名字、图谱却给它连了关系」的情况。
- 抽查发现：无
- 歧义 key：堆内存属性与dma缓冲→keep-separate，pytest→tool:pytest，selenium→tool:selenium
- 剔除：0 条（其中级联剔除 0 条边）
