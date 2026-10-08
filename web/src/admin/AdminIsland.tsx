/** @jsxImportSource preact */
// Picks the admin API: the web API's address, or "mock" in preview builds.
import { httpAdminApi, pretendAdminApi } from "./api";
import Admin from "./Admin";

const base: string = import.meta.env.PUBLIC_API_BASE ?? "/api";
const api = base === "mock" ? pretendAdminApi() : httpAdminApi(base);

export default function AdminIsland() {
  return <Admin api={api} search={typeof window === "undefined" ? "" : window.location.search} />;
}
