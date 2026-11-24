/**
 * Draws a mind map with React Flow. Stateless about storage: the live editor
 * (MindMapPage) and the version preview both feed it plain nodes.
 */
import "@xyflow/react/dist/style.css";

import {
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import { memo, useEffect, useMemo, useRef, useState } from "react";

import { useTheme, type Theme } from "@/charts/palette";

import { branchColor, descendants, ROOT_ID, type MindNode } from "./model";

/** Branch colours: the chart palette's categorical hues, stepped for each theme. */
const BRANCH_HEX: Record<Theme, Record<string, string>> = {
  light: { blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a", yellow: "#eda100", magenta: "#e87ba4", green: "#008300", violet: "#6250d6", red: "#e34948" },
  dark: { blue: "#3987e5", orange: "#d95926", aqua: "#199e70", yellow: "#c98500", magenta: "#d55181", green: "#0ca30c", violet: "#9085e9", red: "#e66767" },
};

export function branchHex(theme: Theme, color: string | null): string {
  return (color && BRANCH_HEX[theme][color]) || (theme === "dark" ? "#71717a" : "#a1a1aa");
}

export type RemoteSelection = { name: string; color: string };

type IdeaData = {
  label: string;
  color: string;
  isRoot: boolean;
  editing: boolean;
  draft: string | null;
  link: { title: string; onOpen: () => void } | null;
  remote: RemoteSelection[];
  onCommit: (label: string) => void;
  onCancel: () => void;
  onTab: (label: string) => void;
};
type IdeaNode = Node<IdeaData, "idea">;

function IdeaEditor({ data }: { data: IdeaData }) {
  const [value, setValue] = useState(data.draft ?? data.label);
  const ref = useRef<HTMLTextAreaElement>(null);
  const done = useRef(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const end = el.value.length;
    el.setSelectionRange(data.draft !== null ? end : 0, end);
  }, [data.draft]);
  const finish = (fn: () => void) => {
    if (done.current) return;
    done.current = true;
    fn();
  };
  return (
    <textarea
      ref={ref}
      aria-label="Idea"
      rows={1}
      value={value}
      maxLength={500}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => finish(() => data.onCommit(value))}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          finish(() => data.onCommit(value));
        } else if (e.key === "Escape") {
          e.preventDefault();
          finish(data.onCancel);
        } else if (e.key === "Tab") {
          e.preventDefault();
          finish(() => data.onTab(value));
        }
      }}
      style={{ fieldSizing: "content" } as React.CSSProperties}
      className="nodrag nopan block min-w-24 max-w-64 resize-none bg-transparent text-center outline-none"
    />
  );
}

const IdeaNodeView = memo(function IdeaNodeView({ data, selected }: NodeProps<IdeaNode>) {
  const ring = data.remote[0]?.color;
  return (
    <div
      className={`relative max-w-72 rounded-xl border-2 px-3 py-1.5 text-center text-sm shadow-sm ${
        data.isRoot ? "px-5 py-3 text-base font-semibold" : ""
      } ${selected ? "ring-2 ring-brand-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-950" : ""} bg-white text-zinc-900 dark:bg-zinc-900 dark:text-zinc-100`}
      style={{
        borderColor: data.color,
        ...(data.isRoot ? { backgroundColor: data.color, color: "white" } : {}),
        ...(ring && !selected ? { boxShadow: `0 0 0 3px ${ring}` } : {}),
      }}
    >
      <Handle type="target" position={Position.Left} id="left" className="!opacity-0" isConnectable={false} />
      <Handle type="source" position={Position.Right} id="right" className="!opacity-0" isConnectable={false} />
      <Handle type="source" position={Position.Left} id="left-out" className="!opacity-0" isConnectable={false} />
      <Handle type="target" position={Position.Right} id="right-in" className="!opacity-0" isConnectable={false} />
      {data.editing ? (
        <IdeaEditor data={data} />
      ) : (
        <div className="whitespace-pre-wrap break-words">{data.label || <span className="opacity-50">Idea</span>}</div>
      )}
      {data.link && !data.editing && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            data.link!.onOpen();
          }}
          className={`nodrag mt-0.5 block w-full truncate text-xs underline-offset-2 hover:underline ${data.isRoot ? "text-white/90" : "text-brand-600"}`}
        >
          ↗ {data.link.title}
        </button>
      )}
      {data.remote.length > 0 && (
        <span className="absolute -top-2.5 right-2 rounded px-1 text-[10px] leading-4 font-medium text-white" style={{ backgroundColor: ring }}>
          {data.remote.map((r) => r.name).join(", ")}
        </span>
      )}
    </div>
  );
});

const NODE_TYPES = { idea: IdeaNodeView };

export type CanvasProps = {
  nodes: MindNode[];
  readOnly: boolean;
  selectedId?: string | null;
  editingId?: string | null;
  /** First character typed to start editing, which replaces the label. */
  draft?: string | null;
  remote?: Map<string, RemoteSelection[]>;
  pageTitle?: (pageId: string) => string | null;
  onOpenPage?: (pageId: string) => void;
  onSelect?: (id: string | null) => void;
  onEdit?: (id: string | null) => void;
  onCommit?: (id: string, label: string) => void;
  onTab?: (id: string, label: string) => void;
  onMove?: (positions: { id: string; x: number; y: number }[]) => void;
  onReparent?: (id: string, parentId: string) => void;
  /** Bump to fit the view again (after a tidy-up, say). */
  fitKey?: number;
};

function FitOnChange({ fitKey }: { fitKey?: number }) {
  const flow = useReactFlow();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void flow.fitView({ padding: 0.2, duration: 300 });
  }, [fitKey, flow]);
  return null;
}

export function MindMapCanvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}

function Canvas(props: CanvasProps) {
  const flow = useReactFlow<IdeaNode>();
  const { nodes, readOnly, selectedId = null, editingId = null, draft = null, remote, pageTitle, onOpenPage } = props;
  const theme = useTheme();
  const handlers = useRef(props);
  handlers.current = props;

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  const built = useMemo<IdeaNode[]>(
    () =>
      nodes.map((n) => {
        const title = n.pageId ? pageTitle?.(n.pageId) : null;
        return {
          id: n.id,
          type: "idea",
          position: { x: n.x, y: n.y },
          selected: n.id === selectedId,
          draggable: !readOnly && n.id !== editingId,
          data: {
            label: n.label,
            color: branchHex(theme, n.parent ? branchColor(byId, n.id) : "violet"),
            isRoot: !n.parent,
            editing: n.id === editingId,
            draft: n.id === editingId ? draft : null,
            link: n.pageId && title != null ? { title: title || "Untitled", onOpen: () => onOpenPage?.(n.pageId!) } : null,
            remote: remote?.get(n.id) ?? [],
            onCommit: (label) => handlers.current.onCommit?.(n.id, label),
            onCancel: () => handlers.current.onEdit?.(null),
            onTab: (label) => handlers.current.onTab?.(n.id, label),
          },
        };
      }),
    [nodes, byId, selectedId, editingId, draft, readOnly, theme, remote, pageTitle, onOpenPage],
  );

  // React Flow keeps measured sizes on our node objects; carry them over between builds.
  const [flowNodes, setFlowNodes] = useState<IdeaNode[]>(built);
  useEffect(() => {
    setFlowNodes((prev) => {
      const measured = new Map(prev.map((n) => [n.id, n.measured]));
      return built.map((n) => ({ ...n, measured: measured.get(n.id) }));
    });
  }, [built]);

  const edges = useMemo<Edge[]>(
    () =>
      nodes
        .filter((n) => n.parent && byId.has(n.parent))
        .map((n) => {
          const parent = byId.get(n.parent!)!;
          const leftward = n.x < parent.x;
          return {
            id: `${n.parent}->${n.id}`,
            source: n.parent!,
            target: n.id,
            sourceHandle: leftward ? "left-out" : "right",
            targetHandle: leftward ? "right-in" : "left",
            type: "default",
            style: { stroke: branchHex(theme, branchColor(byId, n.id)), strokeWidth: 2 },
          };
        }),
    [nodes, byId, theme],
  );

  const onNodesChange = (changes: NodeChange<IdeaNode>[]) => {
    setFlowNodes((prev) => applyNodeChanges(changes.filter((c) => c.type !== "select"), prev));
    if (readOnly) return;
    const moves: { id: string; x: number; y: number }[] = [];
    for (const change of changes) {
      if (change.type !== "position" || !change.position) continue;
      const node = byId.get(change.id);
      if (!node) continue;
      const dx = change.position.x - node.x;
      const dy = change.position.y - node.y;
      if (!dx && !dy) continue;
      // Dragging an idea drags everything that hangs off it.
      for (const id of descendants(nodes, change.id)) {
        const d = byId.get(id);
        if (d) moves.push({ id, x: d.x + dx, y: d.y + dy });
      }
    }
    if (moves.length) handlers.current.onMove?.(moves);
  };

  return (
    <ReactFlow<IdeaNode>
      nodes={flowNodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      onNodesChange={onNodesChange}
      onNodeClick={(_, node) => handlers.current.onSelect?.(node.id)}
      onNodeDoubleClick={(_, node) => !readOnly && handlers.current.onEdit?.(node.id)}
      onPaneClick={() => handlers.current.onSelect?.(null)}
      onNodeDragStart={(_, node) => handlers.current.onSelect?.(node.id)}
      onNodeDragStop={(_, node) => {
        if (readOnly || node.id === ROOT_ID) return;
        // Dropping an idea onto another one moves it (and its subtree) under that idea.
        const own = descendants(nodes, node.id);
        const target = flow.getIntersectingNodes(node).find((n) => !own.has(n.id));
        if (target && target.id !== byId.get(node.id)?.parent) handlers.current.onReparent?.(node.id, target.id);
      }}
      nodesConnectable={false}
      nodesDraggable={!readOnly}
      elementsSelectable
      deleteKeyCode={null}
      selectionKeyCode={null}
      multiSelectionKeyCode={null}
      zoomOnDoubleClick={false}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1.2 }}
      minZoom={0.2}
      maxZoom={2}
      colorMode={theme}
      proOptions={{ hideAttribution: false }}
    >
      <Background gap={24} size={1} />
      <Controls showInteractive={false} position="bottom-left" />
      <MiniMap pannable zoomable position="bottom-right" nodeColor={(n) => (n.data as IdeaData).color} className="!hidden sm:!block" />
      <FitOnChange fitKey={props.fitKey} />
    </ReactFlow>
  );
}
