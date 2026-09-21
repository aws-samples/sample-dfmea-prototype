// ui/src/components/GateReviewDrawer.tsx
// Left slide-out panel showing gate-specific review data before Approve/Reject.

import { useEffect, useState } from "react";
import { apiGet } from "../lib/api";

interface Finding {
  finding_id: string;
  agent: string;
  finding_type: string;
  affected_component: string;
  severity: number | string;
  occurrence: number | string;
  detection: number | string;
  action_priority: string;
  description: string;
  confidence?: number | string;
}

interface Review {
  review_id: string;
  assembly_name: string;
  file_key: string;
  row_count?: number | string;
  status: string;
  created_at: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  reviewId: string;
  gateNumber: number;
  gateLabel: string;
  review: Review | null;
}

const AGENT_COLORS: Record<string, string> = {
  "failure_mode": "bg-red-100 text-red-800",
  "structural":   "bg-blue-100 text-blue-800",
  "regulatory":   "bg-purple-100 text-purple-800",
  "other":        "bg-gray-100 text-gray-700",
  "analyst":      "bg-yellow-100 text-yellow-800",
};

const AGENT_LABELS: Record<string, string> = {
  "failure_mode": "Failure Mode",
  "structural":   "Structural",
  "regulatory":   "Regulatory",
  "other":        "Schema",
  "analyst":      "Analyst",
};

const AP_STYLES: Record<string, string> = {
  H: "bg-red-100 text-red-800 font-bold",
  M: "bg-yellow-100 text-yellow-800 font-bold",
  L: "bg-green-100 text-green-800 font-bold",
};

// ─── Gate 1: Intake summary ───────────────────────────────────────────────────
function Gate1Content({ review }: { review: Review | null }) {
  if (!review) return <p className="text-sm text-gray-400">Loading…</p>;
  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500 leading-relaxed">
        Review the parsed intake details below. Approve if the file was parsed correctly
        and the row count looks right. Reject if the wrong file was uploaded or fields are missing.
      </p>
      <table className="w-full text-sm border-collapse">
        <tbody>
          {[
            ["Assembly",    review.assembly_name],
            ["File",        review.file_key.split("/").pop() ?? review.file_key],
            ["Rows parsed", String(review.row_count ?? "—")],
            ["Submitted",   new Date(review.created_at).toLocaleString()],
            ["Status",      review.status.replace(/_/g, " ")],
          ].map(([label, value]) => (
            <tr key={label} className="border-b border-gray-100">
              <td className="py-2 pr-4 font-semibold text-gray-600 whitespace-nowrap w-32">{label}</td>
              <td className="py-2 text-gray-800 break-all">{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="rounded-lg bg-blue-50 border border-blue-200 p-3 text-xs text-blue-800">
        <strong>What to check:</strong> Does the assembly name match the uploaded file?
        Is the row count consistent with the number of DFMEA rows in the spreadsheet?
      </div>
    </div>
  );
}

// ─── Gate 2: CAD extraction summary ──────────────────────────────────────────
interface CadResult {
  review_id: string;
  cad_key: string | null;
  anchor_count: number;
  anchors: Array<{ label: string; confidence: number; x: number; y: number }>;
  dfmea_components: Array<{ part_name: string; row_count: number }>;
}

function Gate2Content({ review, reviewId }: { review: Review | null; reviewId: string }) {
  const [cad, setCad]       = useState<CadResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiGet<CadResult>(`/reviews/${reviewId}/cad`)
      .then(d => { setCad(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [reviewId]);

  if (!review) return <p className="text-sm text-gray-400">Loading…</p>;

  const components = cad?.dfmea_components ?? [];

  return (
    <div className="space-y-4">
      <p className="text-xs text-gray-500 leading-relaxed">
        The structural decomposition Lambda has parsed the uploaded DFMEA file and extracted
        the component hierarchy. Approve if the part names and row counts look correct for
        this assembly.
      </p>

      {/* Review metadata */}
      <table className="w-full text-sm border-collapse">
        <tbody>
          {[
            ["Assembly",          review.assembly_name],
            ["DFMEA rows",        String(review.row_count ?? "—")],
            ["Unique components", loading ? "…" : String(components.length)],
          ].map(([label, value]) => (
            <tr key={label} className="border-b border-gray-100">
              <td className="py-2 pr-4 font-semibold text-gray-600 whitespace-nowrap w-40">{label}</td>
              <td className="py-2 text-gray-800">{value}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {loading && <p className="text-xs text-gray-400">Loading component data…</p>}

      {/* DFMEA component hierarchy table */}
      {!loading && components.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-700 mb-1">Components Extracted from DFMEA</p>
          <div className="max-h-64 overflow-y-auto rounded border border-gray-200">
            <table className="w-full text-xs border-collapse">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="py-1.5 px-3 text-left font-semibold text-gray-500 border-b border-gray-200">#</th>
                  <th className="py-1.5 px-3 text-left font-semibold text-gray-500 border-b border-gray-200">Part / Component</th>
                  <th className="py-1.5 px-3 text-right font-semibold text-gray-500 border-b border-gray-200">Rows</th>
                </tr>
              </thead>
              <tbody>
                {components.map((c, i) => (
                  <tr key={c.part_name} className={i % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                    <td className="py-1.5 px-3 text-gray-400">{i + 1}</td>
                    <td className="py-1.5 px-3 text-gray-800">{c.part_name}</td>
                    <td className="py-1.5 px-3 text-right text-gray-500">{c.row_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && components.length === 0 && (
        <div className="rounded-lg bg-yellow-50 border border-yellow-200 p-3 text-xs text-yellow-800">
          No component data available. The normalised rows file may not yet be ready.
        </div>
      )}

      <div className="rounded-lg bg-blue-50 border border-blue-200 p-3 text-xs text-blue-800">
        <strong>What to check:</strong> Are all expected sub-assemblies and interfaces present?
        Does the component count and row distribution match the physical assembly structure?
      </div>
    </div>
  );
}

// ─── Gate 3 & 4: Findings table ───────────────────────────────────────────────
function FindingsContent({ reviewId, gateNumber }: { reviewId: string; gateNumber: number }) {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [activeAgent, setActiveAgent] = useState<string>("all");

  useEffect(() => {
    apiGet<{ findings: Finding[] }>(`/reviews/${reviewId}/findings`)
      .then(d => { setFindings(d.findings); setLoading(false); })
      .catch(() => setLoading(false));
  }, [reviewId]);

  if (loading) return <p className="text-sm text-gray-400 py-4">Loading findings…</p>;
  if (findings.length === 0) return <p className="text-sm text-gray-400 py-4">No findings yet.</p>;

  // AP counts
  const apCounts = findings.reduce((acc, f) => {
    const ap = (f.action_priority ?? "L").toUpperCase();
    acc[ap] = (acc[ap] ?? 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  // Agent counts
  const agents = [...new Set(findings.map(f => f.agent))];
  const agentCounts = Object.fromEntries(agents.map(a => [a, findings.filter(f => f.agent === a).length]));

  const filtered = activeAgent === "all" ? findings : findings.filter(f => f.agent === activeAgent);

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500 leading-relaxed">
        {gateNumber === 3
          ? "Review the raw findings produced by each AI agent. Check for hallucinations, incorrect S/O/D scores, or findings that don't apply to this assembly."
          : "Review the consolidated findings before approving PDF report generation. Check AP ratings and ensure the finding set is complete."}
      </p>

      {/* AP summary pills */}
      <div className="flex gap-2">
        {["H", "M", "L"].map(ap => (
          <div key={ap} className={`px-3 py-1.5 rounded-lg text-sm font-semibold ${AP_STYLES[ap]}`}>
            {ap === "H" ? "High" : ap === "M" ? "Medium" : "Low"}: {apCounts[ap] ?? 0}
          </div>
        ))}
        <div className="px-3 py-1.5 rounded-lg text-sm font-semibold bg-gray-100 text-gray-700 ml-auto">
          Total: {findings.length}
        </div>
      </div>

      {/* Agent filter tabs */}
      <div className="flex flex-wrap gap-1.5">
        <button
          onClick={() => setActiveAgent("all")}
          className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
            activeAgent === "all"
              ? "bg-gray-800 text-white border-gray-800"
              : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"
          }`}
        >
          All ({findings.length})
        </button>
        {agents.map(agent => (
          <button
            key={agent}
            onClick={() => setActiveAgent(agent)}
            className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
              activeAgent === agent
                ? "bg-gray-800 text-white border-gray-800"
                : "bg-white text-gray-600 border-gray-300 hover:bg-gray-50"
            }`}
          >
            {AGENT_LABELS[agent] ?? agent} ({agentCounts[agent]})
          </button>
        ))}
      </div>

      {/* Findings list */}
      <div className="space-y-2 max-h-[calc(100vh-340px)] overflow-y-auto pr-1">
        {filtered.map(f => (
          <div key={f.finding_id} className="border border-gray-200 rounded-lg p-3 bg-white hover:bg-gray-50 text-xs">
            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
              <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${AP_STYLES[f.action_priority?.toUpperCase()] ?? "bg-gray-100 text-gray-700"}`}>
                AP: {f.action_priority?.toUpperCase() ?? "—"}
              </span>
              <span className={`px-2 py-0.5 rounded-full text-xs ${AGENT_COLORS[f.agent] ?? "bg-gray-100 text-gray-700"}`}>
                {AGENT_LABELS[f.agent] ?? f.agent}
              </span>
              <span className="px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-600">
                {f.finding_type?.replace(/_/g, " ")}
              </span>
              <span className="ml-auto text-gray-400 font-mono text-xs">
                S{f.severity} O{f.occurrence} D{f.detection}
              </span>
            </div>
            {f.affected_component && (
              <p className="text-gray-500 mb-1">
                <span className="font-semibold text-gray-700">Component:</span> {f.affected_component}
              </p>
            )}
            <p className="text-gray-700 leading-relaxed">{f.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main drawer ──────────────────────────────────────────────────────────────
export default function GateReviewDrawer({ open, onClose, reviewId, gateNumber, gateLabel, review }: Props) {
  // Prevent body scroll when open
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 bg-black/40 z-40 transition-opacity duration-300 ${
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
      />

      {/* Drawer — slides in from LEFT */}
      <div
        className={`fixed top-0 left-0 h-full w-[480px] max-w-[92vw] bg-white z-50 shadow-2xl
          flex flex-col transition-transform duration-300 ease-in-out ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50 shrink-0">
          <div>
            <p className="text-xs text-gray-400 uppercase tracking-wide font-semibold">Review before deciding</p>
            <h2 className="text-base font-bold text-gray-900 mt-0.5">{gateLabel}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-gray-200 text-gray-500 hover:text-gray-800 transition-colors"
            aria-label="Close"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {gateNumber === 1 && <Gate1Content review={review} />}
          {gateNumber === 2 && <Gate2Content review={review} reviewId={reviewId} />}
          {(gateNumber === 3 || gateNumber === 4) && (
            <FindingsContent reviewId={reviewId} gateNumber={gateNumber} />
          )}
        </div>

        {/* Footer hint */}
        <div className="px-5 py-3 border-t border-gray-200 bg-gray-50 shrink-0">
          <p className="text-xs text-gray-400">
            Close this panel, then choose an approval action on the gate card.
          </p>
        </div>
      </div>
    </>
  );
}
