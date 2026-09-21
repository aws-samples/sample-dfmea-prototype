// dfmea-prototype/ui/src/components/SearchWidget.tsx
import { useState } from "react";
import { useAuthStore } from "../stores/authStore";

interface SearchHit {
  key: string;
  excerpt: string;
  score: number;
}

interface SearchResult {
  query: string;
  hits: SearchHit[];
  total: number;
}

export default function SearchWidget() {
  const [query, setQuery]       = useState("");
  const [results, setResults]   = useState<SearchResult | null>(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [drawerOpen, setDrawer] = useState(false);
  const token = useAuthStore((s) => s.token);

  const apiBase = import.meta.env.VITE_API_URL ?? "";

  async function search() {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(
        `${apiBase}/search?q=${encodeURIComponent(query)}&size=10`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: SearchResult = await resp.json();
      setResults(data);
      setDrawer(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {/* Search bar */}
      <div className="flex gap-2 mb-4">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && search()}
          placeholder="Search regulatory evidence..."
          className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={search}
          disabled={loading}
          className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "Searching..." : "Search"}
        </button>
      </div>
      {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

      {/* Evidence drawer */}
      {drawerOpen && results && (
        <div className="fixed inset-y-0 right-0 w-96 bg-white shadow-xl z-50 flex flex-col">
          <div className="flex justify-between items-center p-4 border-b">
            <h2 className="font-semibold text-gray-800">
              Evidence: "{results.query}" ({results.total} results)
            </h2>
            <button
              onClick={() => setDrawer(false)}
              className="text-gray-400 hover:text-gray-600 text-xl"
            >
              &times;
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {results.hits.length === 0 && (
              <p className="text-gray-500 text-sm">No results found.</p>
            )}
            {results.hits.map((hit, i) => (
              <div key={i} className="border rounded p-3 text-sm">
                <p className="font-medium text-blue-700 mb-1 truncate">{hit.key}</p>
                <p className="text-gray-600 leading-relaxed">{hit.excerpt}</p>
                <p className="text-gray-400 text-xs mt-1">Score: {hit.score.toFixed(3)}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
