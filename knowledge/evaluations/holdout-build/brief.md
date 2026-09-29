# 新留出集起草说明（holdout-build）

## 为什么重做一份题集

原 `questions-test.json` 已消费三次（24/32 → 26/32 → 27/32），且复核发现它自身有设计缺陷
（T12 与 dev 的 D02 是重复题、T21 题面没有职业名无法独立检索）。**它只作历史**。
本集是新的验收载体，必须从设计阶段就干净。

## 你的产出

一个文件：`knowledge/evaluations/holdout-build/draft-<你的片名>.json`

```json
{
  "shard": "<片名>",
  "questions": [ /* 见下 */ ]
}
```

可回答题（字段结构照抄，值自己写）：

```json
{
  "questionId": "ZA01",
  "split": "holdout",
  "category": "嵌入式开发",
  "question": "<中文提问>",
  "answerable": true,
  "referenceChunks": ["S16#s291~2", "S16#s292"],
  "gradingNotes": "<答案要点，用中文分号分隔；每条都要能在你选的两段里找到出处>",
  "sourceId": "S16",
  "layer": "xl"
}
```

拒答题：

```json
{
  "questionId": "RH01",
  "split": "holdout",
  "category": "超范围",
  "answerable": false,
  "type": "实时数据",
  "question": "<中文提问>",
  "expectation": "<应明确说明资料不足的原因>"
}
```

`minRefCjkRatio` / `meanRefCjkRatio` **不用你填**，父级统一重算。
`layer` 要你填，判定见下。

## 硬规则（违反即作废）

1. **恰 2 段**参考答案。
2. 两段都必须来自**你那份 `material-<片名>.json` 的 `candidates`**。那份材料已经过四重筛选：
   没被 dev/test 当参考答案用过、不在图谱引用集合内（会泄漏）、与已用段**原文区间不重叠**
   （语料是 ~1500 字重叠窗口，不排掉就会挑到同一段正文的两个窗口）、
   并且**同一 sourceId 内任意两段的 `docIndex` 差都是 3 的倍数且 ≥3**。
3. 两段**同来源**（`sourceId` 必须相同）。
4. 两段在原文顺序上**间隔 ≥3**：用每段自带的 **`docIndex`** 之差判断。
   ⚠ **不要用 candidates 数组下标**——数组是按来源轮转拼出来的，不等于原文顺序；
   之前就有起草人按数组下标核（看着差 ≥3），实际原文只差 1–2，被校验器打回。
5. 参考答案必须**真的能回答**你写的问题——逐段读全文，不要只看小节标题。

## layer 判定（现有 dev/test 的唯一规则，100% 一致）

- **xl**：两段中**至少一段**的 `cjkRatio < 0.20`
- **zh**：两段的 `cjkRatio` 都 ≥ 0.20

注意：`layer` 描述的是**答案所在段落的语言**，不是提问语言。**所有题都用中文提问**，包括 xl 题。

## 题目质量要求

- **必须自足**：只看题面就能定位答案。**不要**写「这个职业……」「上一题提到的……」
  （旧集 T21 就是这么坏掉的——题干里没有职业名，无法独立检索）。
- 一题一个考点，可以有两个子问（现有题的风格就是这样）。
- 难度要**真的读了原文才能答**。不要考「文档标题级」的东西，也不要写那种不看语料也能
  凭常识猜的题。
- `category` 从这几个里选：`嵌入式开发` / `AI框架与推理` / `计算机视觉` / `测试自动化` /
  `职业能力` / `标准与政策`。
- `questionId` 必须用**分配给你的前缀**（见下），避免撞号。

## 纪律（很重要）

- **不要打开 `questions-dev.json` / `questions-test.json` 看题目内容**。留出集必须独立；
  材料已经替你排掉了重叠的段，不需要也不应该去参考旧题。
- **不要编造语料里没有的内容**。`gradingNotes` 里的每一条都要能在你选的两段原文里指出来。
- 不要挑「标题看着像」的段交差；要挑**正文里真的有答案**的段。

## 自检（交付前必须跑）

```bash
python knowledge/eval/validate_questionset.py --set knowledge/evaluations/holdout-build/draft-<片名>.json
```

必须输出「✓ 硬规则全部通过」。退出码 0。
（该命令把 draft 当独立题集检查；与 dev/test 的重叠已由材料生成阶段排除，但你若手改了
`referenceChunks`，就要自己重新核对它确实来自 material 文件。）
