import type { Occupation, TrainingTask } from "../../types/view-models/product";

export const occupations: Occupation[] = [
  {
    id: "edge-ai", name: "边缘AI应用工程师", shortName: "边缘AI应用",
    tasks: { ai: ["资料整理", "基础代码生成", "测试日志归纳"], collaborate: ["模型部署", "异常定位", "方案比较"], human: ["需求判断", "系统权衡", "跨团队沟通"] },
    reason: [
      { label: "已有基础", value: "嵌入式开发、机器视觉项目", state: "confirmed" },
      { label: "可迁移经验", value: "软硬件联调、问题排查", state: "transfer" },
      { label: "仍需验证", value: "是否喜欢模型部署与工程决策", state: "verify" },
    ],
    skills: ["问题分析", "系统思维", "沟通表达"], knowledge: ["计算机与电子", "工程与技术"], tools: ["Python", "Linux", "CamMV"],
  },
  {
    id: "embedded", name: "嵌入式开发工程师", shortName: "嵌入式开发",
    tasks: { ai: ["代码补全", "文档检索", "测试样例生成"], collaborate: ["驱动调试", "性能分析", "故障诊断"], human: ["架构取舍", "可靠性判断", "跨硬件协同"] },
    reason: [
      { label: "已有基础", value: "STM32、C/C++、传感器采集", state: "confirmed" },
      { label: "可迁移经验", value: "视觉项目中的设备联调", state: "transfer" },
      { label: "仍需验证", value: "是否享受底层调试与长期优化", state: "verify" },
    ],
    skills: ["底层调试", "可靠性", "工程协同"], knowledge: ["数字电路", "操作系统"], tools: ["C/C++", "FreeRTOS", "Keil"],
  },
  {
    id: "vision", name: "机器视觉应用工程师", shortName: "机器视觉应用",
    tasks: { ai: ["数据清洗", "标注辅助", "实验报告生成"], collaborate: ["模型调优", "缺陷分析", "部署验证"], human: ["场景建模", "指标权衡", "业务沟通"] },
    reason: [
      { label: "已有基础", value: "视觉识别项目、数字图像处理", state: "confirmed" },
      { label: "可迁移经验", value: "嵌入式设备与相机联调", state: "transfer" },
      { label: "仍需验证", value: "是否喜欢数据实验与现场交付", state: "verify" },
    ],
    skills: ["实验设计", "异常分析", "需求澄清"], knowledge: ["图像处理", "机器学习"], tools: ["Python", "OpenCV", "PyTorch"],
  },
];

export const pathStages = [
  { period: "现在", title: "已有起点", color: "blue", goal: "STM32、开发板、视觉识别、软硬件联调", learning: "C/C++基础、嵌入式系统、数字图像处理", practice: "小车视觉识别、传感器采集与驱动", evidence: "课程作业、实验报告、作品演示", milestone: "确认已有证据" },
  { period: "0—1年", title: "完成边缘AI部署", color: "teal", goal: "Python与Linux、模型转换、设备部署", learning: "Python进阶、Linux基础、模型轻量化与转换", practice: "将模型部署到边缘设备并运行", evidence: "部署记录、性能测试、项目演示", milestone: "完成1个可展示项目" },
  { period: "1—3年", title: "负责系统联调", color: "blue", goal: "性能优化、异常定位、跨团队协作", learning: "性能分析、调试方法、协作流程与工具", practice: "定位并优化系统问题、推动联调", evidence: "问题复盘、优化方案、协作记录", milestone: "独立负责一个模块" },
  { period: "3—5年", title: "形成工程决策能力", color: "teal", goal: "需求判断、系统权衡、人机协作", learning: "系统设计、权衡方法、AI与工程结合", practice: "主导方案设计与落地", evidence: "方案文档、落地效果、复盘总结", milestone: "负责完整解决方案" },
];

export const trainingTasks: TrainingTask[] = [
  { id: "edge-debug", title: "边缘AI设备异常定位", type: "职场模拟", description: "设备识别结果波动，需要定位问题根因。", target: "问题拆解与工程判断", output: "问题定位结论与验证依据", evidence: "问题拆解、AI结果核验、工程判断", duration: "30 分钟", level: "中等", direction: "边缘AI" },
  { id: "ai-check", title: "AI辅助办公结果核验", type: "职场模拟", description: "AI生成的报告存在数据或逻辑偏差。", target: "AI结果核验与批判性思维", output: "核验清单与修正结论", evidence: "AI结果核验、数据核验", duration: "20 分钟", level: "入门", direction: "人机协作" },
  { id: "cross-team", title: "跨团队需求沟通", type: "职场模拟", description: "需求理解存在偏差，影响协作进度。", target: "沟通表达与需求澄清", output: "需求澄清记录与对齐方案", evidence: "沟通表达、需求澄清", duration: "25 分钟", level: "中等", direction: "沟通表达" },
];

export const profileEvidence = [
  { title: "STM32与外设开发", level: "多源支持", source: "项目文档、代码提交、导师反馈", state: "supported" },
  { title: "机器视觉项目经验", level: "多源支持", source: "项目报告、演示记录、导师反馈", state: "supported" },
  { title: "Python与CamMV工具使用", level: "单次观察", source: "课堂表现", state: "observed" },
  { title: "边缘AI模型部署", level: "单次观察", source: "训练任务、代码运行记录", state: "verifying" },
];

export const growthTimeline = [
  { date: "05-10", title: "完成能力初评", detail: "完成首次能力证据收集与分析", color: "blue" },
  { date: "05-15", title: "完成边缘AI异常定位模拟", detail: "通过模拟任务，验证问题定位与分析能力", color: "teal" },
  { date: "05-15", title: "新增问题拆解证据", detail: "在任务中体现对问题拆解与结构化分析能力", color: "purple" },
  { date: "05-16", title: "成长路径调整", detail: "根据最新表现，调整下一阶段成长重点", color: "orange" },
];
