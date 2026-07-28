import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

interface Article {
  id: string;
  category_id: string | null;
  title: string;
  slug: string;
  status: "draft" | "published";
  updated_at: string;
}

export default function Kb() {
  const { currentWorkspace } = useAuth();
  const [articles, setArticles] = useState<Article[]>([]);

  const load = useCallback(async () => {
    if (!currentWorkspace) return;
    const data = await api.get<{ articles: Article[] }>(`/api/workspaces/${currentWorkspace.id}/kb/articles`);
    setArticles(data.articles);
  }, [currentWorkspace]);

  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  const createArticle = async () => {
    if (!currentWorkspace) return;
    const res = await api.post<{ article: Article }>(`/api/workspaces/${currentWorkspace.id}/kb/articles`, {
      title: "Untitled article",
      body_md: "",
    });
    window.location.href = `/kb/${res.article.id}`;
  };

  if (!currentWorkspace) return null;

  return (
    <div className="max-w-3xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-xl font-semibold">Knowledge base</h1>
        <button className="bg-black text-white rounded px-4 py-2 text-sm font-medium" onClick={createArticle}>
          New article
        </button>
      </div>

      {articles.length === 0 ? (
        <div className="text-sm text-gray-400 border rounded p-8 text-center">
          No articles yet. Create one to start building your public help center.
        </div>
      ) : (
        <div className="border rounded divide-y bg-white">
          {articles.map((a) => (
            <Link key={a.id} to={`/kb/${a.id}`} className="flex justify-between items-center px-4 py-3 hover:bg-gray-50">
              <span className="text-sm font-medium">{a.title}</span>
              <span
                className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
                  a.status === "published" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"
                }`}
              >
                {a.status}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
