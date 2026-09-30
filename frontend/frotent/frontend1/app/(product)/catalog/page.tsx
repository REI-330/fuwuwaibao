import { CatalogBrowser } from "../../../components/work-map/catalog-browser";

/**
 * 职业与技能目录（M1-5）。
 *
 * 此前这里是 `redirect("/growth")` —— 组件早就写好了，只是没挂上路由，
 * 于是「五个核心入口」实际只通三个。现在直接渲染组件，数据来自
 * `GET /api/v1/occupations`、`/api/v1/skills`、`/api/v1/catalog/stats`。
 */
export default function CatalogPage() {
  return <CatalogBrowser />;
}
