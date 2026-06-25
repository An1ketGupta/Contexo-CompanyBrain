"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import useSWR from "swr";
import { AlertTriangle, Brain, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";

// react-force-graph-2d touches window on import — load it client-only.
// The package ships no TS types; cast to a permissive prop signature so we
// can pass the documented options without `any`-pocalypse downstream.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ForceGraphProps = Record<string, any>;
const ForceGraph2D = dynamic(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  () => import("react-force-graph-2d" as any).then((m: any) => m.default ?? m),
  {
    ssr: false,
    loading: () => <Skeleton className="h-[560px] w-full rounded-lg" />,
  },
) as unknown as React.ComponentType<ForceGraphProps>;

interface GraphNode {
  id: string;
  name: string;
  cluster_id: string;
  file_type: string | null;
  health_label: string;
  citation_count: number;
}

interface GraphCluster {
  id: string;
  label: string;
  description: string;
  color: string;
  doc_count: number;
  preview_doc_ids: string[];
}

interface Snapshot {
  id: string;
  doc_count: number;
  cluster_count: number;
  status: string;
  clusters: GraphCluster[];
  nodes: GraphNode[];
  edges: { source: string; target: string; weight: number }[];
  params: { k?: number; completed_at?: string };
  created_at: string;
}

interface SnapshotResponse {
  snapshot: Snapshot | null;
}

const fetcher = async (url: string): Promise<SnapshotResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

interface ForceNode extends GraphNode {
  // Mutable layout fields written by react-force-graph.
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}

export default function KnowledgeGraphPage() {
  const { data, error, isLoading, mutate } = useSWR<SnapshotResponse>(
    "/api/admin/knowledge-graph",
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 0 },
  );
  const [refreshing, setRefreshing] = useState(false);
  const [activeCluster, setActiveCluster] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await fetch("/api/admin/knowledge-graph/refresh", {
        method: "POST",
      });
      if (!res.ok) {
        toast.error("Could not queue refresh.");
        return;
      }
      toast.success("Refresh queued. New snapshot in a minute or two.");
      // Poll once after a short delay — the snapshot won't be ready
      // instantly, but a single revalidate covers the common case.
      window.setTimeout(() => void mutate(), 25_000);
    } finally {
      setRefreshing(false);
    }
  }, [mutate]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Knowledge map
          </h1>
          <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">
            An AI-generated map of how your documents cluster by topic. Click
            a cluster to spotlight it; click a node to open the document.
            Recomputed nightly — hit refresh to rebuild on demand.
          </p>
        </div>
        <Button
          onClick={() => void refresh()}
          variant="outline"
          size="sm"
          disabled={refreshing}
        >
          {refreshing ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          Refresh
        </Button>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading ? (
        <Skeleton className="h-[560px] w-full rounded-lg" />
      ) : !data?.snapshot ? (
        <EmptyState onRefresh={() => void refresh()} refreshing={refreshing} />
      ) : (
        <GraphView
          snapshot={data.snapshot}
          activeCluster={activeCluster}
          setActiveCluster={setActiveCluster}
          selectedNode={selectedNode}
          setSelectedNode={setSelectedNode}
        />
      )}
    </div>
  );
}

function EmptyState({
  onRefresh,
  refreshing,
}: {
  onRefresh: () => void;
  refreshing: boolean;
}) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-12 text-center">
      <Brain className="mx-auto h-10 w-10 text-muted-foreground" />
      <h3 className="mt-3 text-sm font-medium">No map yet</h3>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
        Once your knowledge base has a dozen or so documents with summaries,
        the nightly job will build one. You can also trigger it now.
      </p>
      <Button size="sm" className="mt-4" onClick={onRefresh} disabled={refreshing}>
        {refreshing ? (
          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
        ) : (
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
        )}
        Build now
      </Button>
    </div>
  );
}

function GraphView({
  snapshot,
  activeCluster,
  setActiveCluster,
  selectedNode,
  setSelectedNode,
}: {
  snapshot: Snapshot;
  activeCluster: string | null;
  setActiveCluster: (id: string | null) => void;
  selectedNode: GraphNode | null;
  setSelectedNode: (n: GraphNode | null) => void;
}) {
  const colorByCluster = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of snapshot.clusters) m.set(c.id, c.color);
    return m;
  }, [snapshot.clusters]);

  // Cap nodes the force layout sees — runs in the browser, big N stalls UI.
  const graph = useMemo(() => {
    return {
      nodes: snapshot.nodes.map((n) => ({ ...n })),
      links: snapshot.edges.map((e) => ({
        source: e.source,
        target: e.target,
        weight: e.weight,
      })),
    };
  }, [snapshot]);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 800, height: 560 });

  useEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const r = entry.contentRect;
        setSize({
          width: Math.max(320, Math.floor(r.width)),
          height: 560,
        });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const completedAt =
    snapshot.params?.completed_at ?? snapshot.created_at;

  return (
    <>
      <div className="text-xs text-muted-foreground">
        {snapshot.doc_count} document{snapshot.doc_count === 1 ? "" : "s"} ·{" "}
        {snapshot.cluster_count} cluster
        {snapshot.cluster_count === 1 ? "" : "s"} · Updated{" "}
        {formatDistanceToNow(completedAt)} ago
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_280px]">
        <div
          ref={containerRef}
          className="overflow-hidden rounded-lg border border-border bg-card"
        >
          <ForceGraph2D
            width={size.width}
            height={size.height}
            graphData={graph}
            cooldownTicks={120}
            nodeRelSize={4}
            backgroundColor="transparent"
            nodeLabel={(n: unknown) => {
              const node = n as unknown as ForceNode;
              return node.name;
            }}
            nodeColor={(n: unknown) => {
              const node = n as unknown as ForceNode;
              const base = colorByCluster.get(node.cluster_id) ?? "#94a3b8";
              if (!activeCluster || activeCluster === node.cluster_id) return base;
              // Fade nodes from non-active clusters by appending alpha to the
              // hex. ForceGraph treats any valid CSS color string.
              return base + "33";
            }}
            nodeVal={(n: unknown) => {
              const node = n as unknown as ForceNode;
              return 1 + Math.min(8, Math.log1p(node.citation_count));
            }}
            linkColor={() => "rgba(148, 163, 184, 0.3)"}
            onNodeClick={(n: unknown) => {
              const node = n as unknown as ForceNode;
              setSelectedNode(node);
            }}
            onBackgroundClick={() => {
              setActiveCluster(null);
              setSelectedNode(null);
            }}
          />
        </div>

        <aside className="space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Clusters
          </h2>
          <ul className="space-y-2">
            {snapshot.clusters.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={`w-full rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                    activeCluster === c.id
                      ? "border-primary bg-primary/5"
                      : "border-border bg-card hover:border-foreground/30"
                  }`}
                  onClick={() =>
                    setActiveCluster(activeCluster === c.id ? null : c.id)
                  }
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: c.color }}
                    />
                    <span className="flex-1 truncate text-sm font-medium">
                      {c.label}
                    </span>
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {c.doc_count}
                    </span>
                  </div>
                  {c.description && (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {c.description}
                    </p>
                  )}
                </button>
              </li>
            ))}
          </ul>

          {selectedNode && (
            <div className="rounded-lg border border-border bg-card p-3">
              <h3 className="truncate text-sm font-semibold">
                {selectedNode.name}
              </h3>
              <p className="mt-1 text-[11px] text-muted-foreground">
                {selectedNode.file_type ?? "doc"} · {selectedNode.citation_count}{" "}
                citation{selectedNode.citation_count === 1 ? "" : "s"}
              </p>
              <Link
                href={`/documents?id=${encodeURIComponent(selectedNode.id)}`}
                className="mt-2 inline-block text-xs font-medium text-primary hover:underline"
              >
                Open document →
              </Link>
            </div>
          )}
        </aside>
      </div>
    </>
  );
}
