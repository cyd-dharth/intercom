import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

export interface WorkspaceMembership {
  id: string;
  name: string;
  slug: string;
  role: "admin" | "agent";
}

export interface User {
  id: string;
  email: string;
  name: string;
}

interface MeResponse {
  user: User;
  workspaces: WorkspaceMembership[];
}

interface AuthState {
  user: User | null;
  workspaces: WorkspaceMembership[];
  currentWorkspace: WorkspaceMembership | null;
  loading: boolean;
  refresh: () => Promise<void>;
  setCurrentWorkspace: (w: WorkspaceMembership) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceMembership[]>([]);
  const [currentWorkspace, setCurrentWorkspaceState] = useState<WorkspaceMembership | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<MeResponse>("/api/auth/me");
      setUser(data.user);
      setWorkspaces(data.workspaces);
      setCurrentWorkspaceState((prev) => prev ?? data.workspaces[0] ?? null);
    } catch {
      setUser(null);
      setWorkspaces([]);
      setCurrentWorkspaceState(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const logout = useCallback(async () => {
    await api.post("/api/auth/logout");
    setUser(null);
    setWorkspaces([]);
    setCurrentWorkspaceState(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        workspaces,
        currentWorkspace,
        loading,
        refresh,
        setCurrentWorkspace: setCurrentWorkspaceState,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
