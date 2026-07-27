import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";

interface Member {
  id: string;
  email: string;
  name: string;
  role: "admin" | "agent";
}

interface PendingInvite {
  id: string;
  email: string;
  role: string;
  expires_at: string;
}

interface TeamResponse {
  members: Member[];
  pending_invites: PendingInvite[];
}

export default function SettingsTeam() {
  const { currentWorkspace, user } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [pending, setPending] = useState<PendingInvite[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "agent">("agent");
  const [error, setError] = useState<string | null>(null);
  const [lastInviteLink, setLastInviteLink] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!currentWorkspace) return;
    const data = await api.get<TeamResponse>(`/api/workspaces/${currentWorkspace.id}/team`);
    setMembers(data.members);
    setPending(data.pending_invites);
  }, [currentWorkspace]);

  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  async function onInvite(e: FormEvent) {
    e.preventDefault();
    if (!currentWorkspace) return;
    setError(null);
    try {
      const res = await api.post<{ invite_token: string }>(`/api/workspaces/${currentWorkspace.id}/team/invites`, {
        email,
        role,
      });
      setLastInviteLink(`${window.location.origin}/invite/${res.invite_token}`);
      setEmail("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    }
  }

  async function onRoleChange(memberId: string, newRole: string) {
    if (!currentWorkspace) return;
    try {
      await api.patch(`/api/workspaces/${currentWorkspace.id}/team/members/${memberId}`, { role: newRole });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    }
  }

  if (!currentWorkspace) return null;

  return (
    <div className="max-w-2xl mx-auto p-8 space-y-8">
      <h1 className="text-xl font-semibold">Team</h1>

      <div>
        <h2 className="font-medium mb-2">Members</h2>
        <table className="w-full text-sm">
          <tbody>
            {members.map((m) => (
              <tr key={m.id} className="border-b">
                <td className="py-2">{m.name}</td>
                <td className="py-2 text-gray-500">{m.email}</td>
                <td className="py-2">
                  <select
                    className="border rounded px-2 py-1"
                    value={m.role}
                    disabled={m.id === user?.id}
                    onChange={(e) => onRoleChange(m.id, e.target.value)}
                  >
                    <option value="admin">Admin</option>
                    <option value="agent">Agent</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pending.length > 0 && (
        <div>
          <h2 className="font-medium mb-2">Pending invites</h2>
          <ul className="text-sm text-gray-500 space-y-1">
            {pending.map((i) => (
              <li key={i.id}>
                {i.email} &middot; {i.role}
              </li>
            ))}
          </ul>
        </div>
      )}

      <form onSubmit={onInvite} className="space-y-3 border-t pt-6">
        <h2 className="font-medium">Invite a teammate</h2>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2">
          <input
            type="email"
            placeholder="email@example.com"
            className="flex-1 border rounded px-3 py-2"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <select className="border rounded px-2 py-2" value={role} onChange={(e) => setRole(e.target.value as "admin" | "agent")}>
            <option value="agent">Agent</option>
            <option value="admin">Admin</option>
          </select>
          <button type="submit" className="bg-black text-white rounded px-4 py-2 font-medium">
            Invite
          </button>
        </div>
        {lastInviteLink && (
          <p className="text-sm text-gray-500">
            Invite link (share manually, no email is sent):{" "}
            <a className="underline break-all" href={lastInviteLink}>
              {lastInviteLink}
            </a>
          </p>
        )}
      </form>
    </div>
  );
}
