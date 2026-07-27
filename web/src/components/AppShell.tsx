import { Link, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

export default function AppShell() {
  const { user, workspaces, currentWorkspace, setCurrentWorkspace, logout } = useAuth();

  return (
    <div className="h-screen flex flex-col">
      <header className="h-12 border-b bg-white flex items-center px-4 justify-between shrink-0">
        <div className="flex items-center gap-4">
          <span className="font-semibold">Inbox</span>
          <nav className="flex gap-3 text-sm text-gray-600">
            <Link to="/inbox">Inbox</Link>
            <Link to="/settings/team">Team</Link>
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {workspaces.length > 1 && (
            <select
              className="border rounded px-2 py-1"
              value={currentWorkspace?.id}
              onChange={(e) => {
                const w = workspaces.find((w) => w.id === e.target.value);
                if (w) setCurrentWorkspace(w);
              }}
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          )}
          <span className="text-gray-500">{user?.name}</span>
          <button onClick={() => logout()} className="text-gray-500 underline">
            Log out
          </button>
        </div>
      </header>
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
