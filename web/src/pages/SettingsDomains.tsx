import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";

interface DnsRecord {
  type: string;
  name: string;
  value: string;
}

interface Domain {
  id: string;
  hostname: string;
  status: "pending" | "verified" | "failed";
  last_checked_at: string | null;
  last_error: string | null;
  created_at: string;
  dns_records: DnsRecord[];
}

const STATUS_STYLES: Record<Domain["status"], string> = {
  verified: "bg-green-100 text-green-700",
  pending: "bg-yellow-100 text-yellow-700",
  failed: "bg-red-100 text-red-700",
};

export default function SettingsDomains() {
  const { currentWorkspace } = useAuth();
  const [domains, setDomains] = useState<Domain[]>([]);
  const [hostname, setHostname] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checkingId, setCheckingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!currentWorkspace) return;
    const data = await api.get<{ domains: Domain[] }>(`/api/workspaces/${currentWorkspace.id}/domains`);
    setDomains(data.domains);
  }, [currentWorkspace]);

  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    if (!currentWorkspace) return;
    setError(null);
    try {
      await api.post(`/api/workspaces/${currentWorkspace.id}/domains`, { hostname });
      setHostname("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    }
  }

  async function onCheckNow(domainId: string) {
    if (!currentWorkspace) return;
    setCheckingId(domainId);
    try {
      await api.post(`/api/workspaces/${currentWorkspace.id}/domains/${domainId}/check`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setCheckingId(null);
    }
  }

  if (!currentWorkspace) return null;

  return (
    <div className="max-w-2xl mx-auto p-8 space-y-8">
      <h1 className="text-xl font-semibold">Custom domains</h1>
      <p className="text-sm text-gray-500">
        Point a domain at your knowledge base. Verification checks DNS only, there is no automatic
        TLS certificate provisioning in this build, see the README for the approach.
      </p>

      <div className="space-y-4">
        {domains.length === 0 && <p className="text-sm text-gray-400">No custom domains yet.</p>}
        {domains.map((d) => (
          <div key={d.id} className="border rounded p-4 bg-white space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{d.hostname}</span>
              <span className={`text-xs rounded px-2 py-1 font-medium ${STATUS_STYLES[d.status]}`}>{d.status}</span>
            </div>

            <table className="w-full text-xs border rounded">
              <thead>
                <tr className="border-b text-left text-gray-400">
                  <th className="py-1 px-2">Type</th>
                  <th className="py-1 px-2">Name</th>
                  <th className="py-1 px-2">Value</th>
                </tr>
              </thead>
              <tbody>
                {d.dns_records.map((r, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="py-1 px-2">{r.type}</td>
                    <td className="py-1 px-2 break-all">{r.name}</td>
                    <td className="py-1 px-2 break-all">{r.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {d.last_error && d.status !== "verified" && (
              <p className="text-xs text-red-600">{d.last_error}</p>
            )}
            <div className="flex items-center justify-between text-xs text-gray-400">
              <span>{d.last_checked_at ? `Last checked ${new Date(d.last_checked_at).toLocaleString()}` : "Not checked yet"}</span>
              <button
                onClick={() => onCheckNow(d.id)}
                disabled={checkingId === d.id}
                className="border rounded px-3 py-1 font-medium text-gray-700 disabled:opacity-50"
              >
                {checkingId === d.id ? "Checking..." : "Check now"}
              </button>
            </div>
          </div>
        ))}
      </div>

      <form onSubmit={onAdd} className="space-y-3 border-t pt-6">
        <h2 className="font-medium">Add a domain</h2>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="help.example.com"
            className="flex-1 border rounded px-3 py-2"
            value={hostname}
            onChange={(e) => setHostname(e.target.value)}
            required
          />
          <button type="submit" className="bg-black text-white rounded px-4 py-2 font-medium">
            Add
          </button>
        </div>
      </form>
    </div>
  );
}
