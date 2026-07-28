import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

interface AiCall {
  id: number;
  kind: string;
  model: string;
  status: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_micros: number;
  created_at: string;
}

interface AiUsage {
  daily_budget_cents: number | null;
  spent_cents_today: number;
  call_count_today: number;
  total_input_tokens: number;
  total_output_tokens: number;
  status_breakdown: Record<string, number>;
  calls: AiCall[];
}

interface JobCounts {
  pending_total: number;
  by_kind_status: { kind: string; status: string; count: number }[];
}

export default function SettingsAi() {
  const { currentWorkspace } = useAuth();
  const [usage, setUsage] = useState<AiUsage | null>(null);
  const [jobs, setJobs] = useState<JobCounts | null>(null);

  const load = useCallback(async () => {
    if (!currentWorkspace) return;
    const [usageRes, jobsRes] = await Promise.all([
      api.get<AiUsage>(`/api/workspaces/${currentWorkspace.id}/admin/ai-usage`),
      api.get<JobCounts>(`/api/workspaces/${currentWorkspace.id}/admin/jobs`),
    ]);
    setUsage(usageRes);
    setJobs(jobsRes);
  }, [currentWorkspace]);

  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  if (!currentWorkspace || !usage) return null;

  return (
    <div className="max-w-3xl mx-auto p-8 space-y-8">
      <h1 className="text-xl font-semibold">AI usage</h1>

      <div className="grid grid-cols-3 gap-4">
        <div className="border rounded p-4 bg-white">
          <div className="text-[11px] uppercase text-gray-400">Spend today</div>
          <div className="text-2xl font-semibold">${(usage.spent_cents_today / 100).toFixed(4)}</div>
          {usage.daily_budget_cents != null && (
            <div className="text-xs text-gray-400">of ${(usage.daily_budget_cents / 100).toFixed(2)} budget</div>
          )}
        </div>
        <div className="border rounded p-4 bg-white">
          <div className="text-[11px] uppercase text-gray-400">Calls today</div>
          <div className="text-2xl font-semibold">{usage.call_count_today}</div>
        </div>
        <div className="border rounded p-4 bg-white">
          <div className="text-[11px] uppercase text-gray-400">Tokens today</div>
          <div className="text-2xl font-semibold">
            {usage.total_input_tokens + usage.total_output_tokens}
          </div>
          <div className="text-xs text-gray-400">
            {usage.total_input_tokens} in / {usage.total_output_tokens} out
          </div>
        </div>
      </div>

      <div>
        <h2 className="font-medium mb-2">Status breakdown</h2>
        <div className="flex gap-2 flex-wrap">
          {Object.entries(usage.status_breakdown).map(([status, count]) => (
            <span key={status} className="text-xs border rounded px-2 py-1 bg-white">
              {status}: {count}
            </span>
          ))}
          {Object.keys(usage.status_breakdown).length === 0 && <span className="text-sm text-gray-400">No AI calls yet today.</span>}
        </div>
      </div>

      {jobs && (
        <div>
          <h2 className="font-medium mb-2">Job queue ({jobs.pending_total} pending)</h2>
          <table className="w-full text-sm border rounded bg-white">
            <thead>
              <tr className="border-b text-left text-gray-400 text-xs">
                <th className="py-2 px-3">Kind</th>
                <th className="py-2 px-3">Status</th>
                <th className="py-2 px-3">Count</th>
              </tr>
            </thead>
            <tbody>
              {jobs.by_kind_status.map((row, i) => (
                <tr key={i} className="border-b last:border-0">
                  <td className="py-2 px-3">{row.kind}</td>
                  <td className="py-2 px-3">{row.status}</td>
                  <td className="py-2 px-3">{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div>
        <h2 className="font-medium mb-2">Recent calls</h2>
        <table className="w-full text-sm border rounded bg-white">
          <thead>
            <tr className="border-b text-left text-gray-400 text-xs">
              <th className="py-2 px-3">Kind</th>
              <th className="py-2 px-3">Model</th>
              <th className="py-2 px-3">Status</th>
              <th className="py-2 px-3">Tokens</th>
              <th className="py-2 px-3">Cost</th>
            </tr>
          </thead>
          <tbody>
            {usage.calls.map((c) => (
              <tr key={c.id} className="border-b last:border-0">
                <td className="py-2 px-3">{c.kind}</td>
                <td className="py-2 px-3">{c.model}</td>
                <td className="py-2 px-3">{c.status}</td>
                <td className="py-2 px-3">
                  {(c.input_tokens || 0) + (c.output_tokens || 0)}
                </td>
                <td className="py-2 px-3">${(c.cost_micros / 1_000_000).toFixed(6)}</td>
              </tr>
            ))}
            {usage.calls.length === 0 && (
              <tr>
                <td className="py-4 px-3 text-gray-400" colSpan={5}>
                  No calls yet today.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
