import { PageHeading } from "../../../components/ui/page-heading";
import { WorkMapExplorer } from "../../../components/work-map/work-map-explorer";
import { CareerMatchPanel } from "../../../components/work-map/career-match-panel";

/**
 * 未来工作地图（M1-5）。
 *
 * 此前是 `redirect("/growth")`；`WorkMapExplorer` 与 `CareerMatchPanel` 两个组件
 * 都已实现、只是没挂上路由。现在：
 * * 图谱探索走 `GET /api/v1/occupations`、`/api/v1/occupations/<id>`、`/api/v1/skills`（真数据）；
 * * 职业匹配区仍读 `/api/career-matches/*`，那三条路由**契约已声明但未实现**，
 *   面板会对 501 明确显示「尚未上线」而不是白屏（见 `CareerMatchPanel`）。
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
