export type TrainingEvaluation = {
  title: string;
  summary: string;
  evidence: string[];
};

export async function evaluateTraining(choice: string, taskId = "edge-debug"): Promise<TrainingEvaluation> {
  await new Promise((resolve) => globalThis.setTimeout(resolve, 560));
  if (taskId !== "edge-debug") {
    const checked = choice.includes("核验") || choice.includes("验收标准");
    return {
      title: checked ? "形成一条候选成长回响" : "这次观察还需要补充",
      summary: taskId === "ai-check"
        ? checked ? "你选择对照原始数据核验，并记录修正依据；实际核验能力还需要实践成果验证。" : "你提出了处理方式，但仍需补充数据来源与核验过程。"
        : checked ? "你选择澄清分歧、约束与验收标准；沟通效果还需要协作记录验证。" : "你提出了处理方式，但仍需补充与相关方达成共识的步骤。",
      evidence: checked ? taskId === "ai-check" ? ["AI结果核验", "数据核验"] : ["需求澄清", "沟通表达"] : ["待补充依据"],
    };
  }
  const strong = choice.includes("约束") || choice.includes("验证");

  return strong
    ? {
        title: "形成一条候选成长回响",
        summary: "你先确认硬件约束，再让AI列出可能原因并逐项验证。",
        evidence: ["问题拆解", "AI结果核验", "工程判断"],
      }
    : {
        title: "这次观察还需要补充",
        summary: "你已经提出行动方向，但还需要说明判断依据与验证顺序。",
        evidence: ["单次观察", "待补充依据"],
      };
}
