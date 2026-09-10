import { requireBattleAdmin } from "../admin-auth";
import AccountForm from "./AccountForm";

export const metadata = { title: "修改管理員帳密｜HanStock" };

export default async function AdminAccountPage() {
  const admin = await requireBattleAdmin("/admin/account");
  return <AccountForm currentUsername={admin.username} />;
}
