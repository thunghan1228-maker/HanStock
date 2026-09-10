import AdminDashboard from "./AdminDashboard";
import { requireBattleAdmin } from "./admin-auth";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "盤中戰鬥版管理後台｜HanStock",
  description: "HanStock 盤中戰鬥版資料、排行與參數管理",
};

export default async function AdminPage() {
  const admin = await requireBattleAdmin("/admin");
  return <AdminDashboard adminName={admin.displayName} adminUsername={admin.username} />;
}
