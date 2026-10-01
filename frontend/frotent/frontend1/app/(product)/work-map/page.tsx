import { PageHeading } from "../../../components/ui/page-heading";
import { WorkMapExplorer } from "../../../components/work-map/work-map-explorer";
import { CareerMatchPanel } from "../../../components/work-map/career-match-panel";

/**
 * 未来工作地图（M1-5）。
 *
 * 此前是 `redirect("/growth")`；`WorkMapExplorer` 与 `CareerMatchPanel` 两个组件
 * 都已实现、只是没挂上路由。现在：
 * * 图谱探索走 `GET /api/v1/occupations`、`/api/v1/occupations/<id>`、`/api/v1/skills`（真数据）；
 * * 职业匹配走 `/api/career-matches/*`（2026-09-30 起已实现，**不再 501**）：排序依据全在
 *   图谱（requires 边带 importance/targetLevel）与已确认画像里，四维打分 + 逐条依据 + 差距/待验证问题。
 *   面板对 501 仍有兜底显示「尚未上线」，用于别的未实现接口。
 */
export default function WorkMapPage() {
  return <div className="xn-stack">
    <PageHeading
      title="未来工作地图"
      subtitle="沿着真实的前置关系看职业与技能：选中节点会亮出它的图邻域、任务与发展信号。"
    />
    <WorkMapExplorer />
    <CareerMatchPanel />
  </div>;
}
