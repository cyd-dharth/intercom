import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

interface Category {
  id: string;
  name: string;
  slug: string;
}

interface Article {
  id: string;
  category_id: string | null;
  title: string;
  slug: string;
  body_md: string;
  status: "draft" | "published";
}

export default function KbEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { currentWorkspace } = useAuth();
  const [article, setArticle] = useState<Article | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [title, setTitle] = useState("");
  const [bodyMd, setBodyMd] = useState("");
  const [categoryId, setCategoryId] = useState<string>("");
  const [previewHtml, setPreviewHtml] = useState("");
  const [saving, setSaving] = useState(false);
  const previewTimer = useRef<number | null>(null);

  const workspaceId = currentWorkspace?.id;

  useEffect(() => {
    if (!workspaceId || !id) return;
    (async () => {
      const [articleRes, categoriesRes] = await Promise.all([
        api.get<{ article: Article }>(`/api/workspaces/${workspaceId}/kb/articles/${id}`),
        api.get<{ categories: Category[] }>(`/api/workspaces/${workspaceId}/kb/categories`),
      ]);
      setArticle(articleRes.article);
      setTitle(articleRes.article.title);
      setBodyMd(articleRes.article.body_md);
      setCategoryId(articleRes.article.category_id || "");
      setCategories(categoriesRes.categories);
    })();
  }, [workspaceId, id]);

  const refreshPreview = useCallback(
    (md: string) => {
      if (!workspaceId) return;
      if (previewTimer.current) window.clearTimeout(previewTimer.current);
      previewTimer.current = window.setTimeout(async () => {
        const res = await api.post<{ html: string }>(`/api/workspaces/${workspaceId}/kb/preview`, { body_md: md });
        setPreviewHtml(res.html);
      }, 300);
    },
    [workspaceId]
  );

  useEffect(() => {
    refreshPreview(bodyMd);
  }, [bodyMd, refreshPreview]);

  const save = async () => {
    if (!workspaceId || !id) return;
    setSaving(true);
    try {
      await api.patch(`/api/workspaces/${workspaceId}/kb/articles/${id}`, {
        title,
        body_md: bodyMd,
        category_id: categoryId || null,
      });
    } finally {
      setSaving(false);
    }
  };

  const togglePublish = async () => {
    if (!workspaceId || !id || !article) return;
    await save();
    if (article.status === "published") {
      await api.post(`/api/workspaces/${workspaceId}/kb/articles/${id}/unpublish`);
      setArticle({ ...article, status: "draft" });
    } else {
      await api.post(`/api/workspaces/${workspaceId}/kb/articles/${id}/publish`);
      setArticle({ ...article, status: "published" });
    }
  };

  const createCategory = async () => {
    if (!workspaceId) return;
    const name = window.prompt("Category name");
    if (!name) return;
    const res = await api.post<{ category: Category }>(`/api/workspaces/${workspaceId}/kb/categories`, { name });
    setCategories((prev) => [...prev, res.category]);
    setCategoryId(res.category.id);
  };

  if (!article) return null;

  return (
    <div className="h-full flex flex-col">
      <div className="border-b px-6 py-3 flex items-center justify-between bg-white">
        <button className="text-sm text-gray-500" onClick={() => navigate("/kb")}>
          &larr; Back
        </button>
        <div className="flex items-center gap-2">
          <span
            className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
              article.status === "published" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"
            }`}
          >
            {article.status}
          </span>
          <button className="border rounded px-3 py-1.5 text-sm" onClick={save} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </button>
          <button className="bg-black text-white rounded px-3 py-1.5 text-sm" onClick={togglePublish}>
            {article.status === "published" ? "Unpublish" : "Publish"}
          </button>
        </div>
      </div>

      <div className="px-6 py-3 border-b bg-white flex gap-3 items-center">
        <input
          className="flex-1 border rounded px-3 py-2 text-lg font-medium"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Article title"
        />
        <select className="border rounded px-2 py-2 text-sm" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
          <option value="">No category</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <button className="text-sm text-gray-500 underline whitespace-nowrap" onClick={createCategory}>
          + New category
        </button>
      </div>

      <div className="flex-1 flex overflow-hidden">
        <textarea
          className="w-1/2 p-4 font-mono text-sm resize-none outline-none border-r"
          value={bodyMd}
          onChange={(e) => setBodyMd(e.target.value)}
          placeholder="Write markdown here..."
        />
        <div className="w-1/2 p-4 overflow-y-auto prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: previewHtml }} />
      </div>
    </div>
  );
}
