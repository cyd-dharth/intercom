import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import { useAuth } from "./auth";
import InviteAccept from "./pages/InviteAccept";
import Inbox from "./pages/Inbox";
import Kb from "./pages/Kb";
import KbEditor from "./pages/KbEditor";
import Login from "./pages/Login";
import SettingsAi from "./pages/SettingsAi";
import SettingsDomains from "./pages/SettingsDomains";
import SettingsTeam from "./pages/SettingsTeam";
import Signup from "./pages/Signup";

function RequireAuth({ children }: { children: React.ReactElement }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="h-screen flex items-center justify-center text-gray-400">Loading...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/signup" element={<Signup />} />
      <Route path="/login" element={<Login />} />
      <Route path="/invite/:token" element={<InviteAccept />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/kb" element={<Kb />} />
        <Route path="/kb/:id" element={<KbEditor />} />
        <Route path="/settings/team" element={<SettingsTeam />} />
        <Route path="/settings/ai" element={<SettingsAi />} />
        <Route path="/settings/domains" element={<SettingsDomains />} />
      </Route>
      <Route path="/" element={<Navigate to="/inbox" replace />} />
    </Routes>
  );
}
