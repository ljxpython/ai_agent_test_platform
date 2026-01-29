import type { UIMessage } from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import type { ReactNode } from "react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type StreamCtx = ReturnType<typeof import("@/providers/Stream").useStreamContext>;

function getIdentityHeaders(): Record<string, string> {
  // 与 ui/src/providers/client.ts 的默认值保持一致
  const userId = process.env.NEXT_PUBLIC_USER_ID ?? "demo";
  const projectId = process.env.NEXT_PUBLIC_PROJECT_ID ?? "default";
  return {
    "X-User-Id": userId,
    "X-Project-Id": projectId,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function getString(obj: Record<string, unknown>, key: string): string | undefined {
  const v = obj[key];
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function getBoolean(
  obj: Record<string, unknown>,
  key: string,
): boolean | undefined {
  const v = obj[key];
  return typeof v === "boolean" ? v : undefined;
}

function getNumber(obj: Record<string, unknown>, key: string): number | undefined {
  const v = obj[key];
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

function formatDuration(durationMs: number | undefined): string | null {
  if (durationMs == null) return null;
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`;
  return `${(durationMs / 1000).toFixed(2)}s`;
}

function clipSql(sql: string, maxLen = 280): string {
  const s = sql.trim();
  if (s.length <= maxLen) return s;
  return `${s.slice(0, maxLen)}\n…`;
}

function previewCell(value: unknown, maxLen = 48): string {
  const raw = value == null ? "null" : String(value);
  const oneLine = raw.replace(/\s+/g, " ").trim();
  if (oneLine.length <= maxLen) return oneLine;
  return `${oneLine.slice(0, maxLen - 1)}…`;
}

function buildTablePreview(params: {
  columns: string[];
  rows: unknown[][];
  maxRows: number;
  maxCols: number;
}): { text: string; hiddenRows: number; hiddenCols: number } {
  const cols = params.columns.slice(0, params.maxCols);
  const hiddenCols = Math.max(0, params.columns.length - cols.length);

  const rows = params.rows.slice(0, params.maxRows);
  const hiddenRows = Math.max(0, params.rows.length - rows.length);

  const header = cols.length > 0 ? cols.join("\t") : "(no columns)";
  const sep = cols.length > 0 ? cols.map(() => "---").join("\t") : "---";

  const lines = [header, sep];
  for (const row of rows) {
    const parts: string[] = [];
    const cellIter = row[Symbol.iterator]();
    for (let colIdx = 0; colIdx < cols.length; colIdx += 1) {
      const next = cellIter.next();
      parts.push(previewCell(next.done ? null : next.value));
    }
    lines.push(parts.join("\t"));
  }

  return { text: lines.join("\n"), hiddenRows, hiddenCols };
}

async function postApprovalDecision(params: {
  threadId: string;
  approvalId: string;
  action: "approve" | "reject";
  sql?: string;
}): Promise<void> {
  const res = await fetch(
    `/api/platform/threads/${encodeURIComponent(params.threadId)}/approvals/${encodeURIComponent(
      params.approvalId,
    )}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getIdentityHeaders(),
      },
      body: JSON.stringify({ action: params.action, sql: params.sql }),
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `approval decision failed (HTTP ${res.status})`);
  }
}

function CardShell({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mt-2 rounded-xl border bg-background/60 p-4 shadow-sm",
        "backdrop-blur supports-[backdrop-filter]:bg-background/40",
        className,
      )}
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm font-semibold">{title}</div>
      </div>
      {children}
    </div>
  );
}

export function InlineUIMsgRenderer({
  ui,
  thread,
}: {
  ui: UIMessage;
  thread: StreamCtx;
}) {
  if (ui.name === "SqlApprovalCard") {
    return (
      <SqlApprovalCard
        ui={ui}
        thread={thread}
      />
    );
  }

  if (ui.name === "ResultTableCard") {
    return <ResultTableCard ui={ui} />;
  }

  return null;
}

function SqlApprovalCard({ ui, thread }: { ui: UIMessage; thread: StreamCtx }) {
  const props = isRecord(ui.props) ? ui.props : {};
  const approvalId = getString(props, "approval_id");
  const proposedSql = getString(props, "sql") ?? "";
  const reason = getString(props, "reason");
  const readOnly = getBoolean(props, "read_only") ?? true;
  const maxRows = getNumber(props, "max_rows");

  const parentRunId =
    (typeof ui.metadata?.run_id === "string" && ui.metadata.run_id) ||
    getString(props, "parent_run_id") ||
    getString(props, "run_id");

  const [mode, setMode] = useState<"view" | "edit">("view");
  const [sqlDraft, setSqlDraft] = useState(proposedSql);
  const [status, setStatus] = useState<"idle" | "approved" | "rejected">("idle");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveSql = mode === "edit" ? sqlDraft : proposedSql;

  const [threadId] = useQueryState("threadId");

  const canAct = !submitting && status === "idle";

  const handleApprove = async () => {
    if (!approvalId) {
      setError("卡片缺少 approval_id，无法提交。请刷新后重试。");
      return;
    }
    if (!threadId) {
      setError("缺少 thread_id，无法提交审批。");
      return;
    }

    const sql = effectiveSql.trim();
    if (!sql) {
      setError("SQL 不能为空。");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      // 先记录审批，再触发 Run-B
      await postApprovalDecision({
        threadId,
        approvalId,
        action: "approve",
        sql,
      });

      void thread.submit(
        // Run-B inputs (MVP): approved_sql + approval_id + parent_run_id
        {
          approved_sql: sql,
          approval_id: approvalId,
          parent_run_id: parentRunId ?? null,
        } as unknown as Parameters<StreamCtx["submit"]>[0],
        {
          streamMode: ["values", "custom"],
          streamSubgraphs: true,
          streamResumable: true,
        } as Parameters<StreamCtx["submit"]>[1],
      );
      setStatus("approved");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg || "提交失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  const handleReject = async () => {
    if (!approvalId) {
      setError("卡片缺少 approval_id，无法提交。请刷新后重试。");
      return;
    }
    if (!threadId) {
      setError("缺少 thread_id，无法提交审批。");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await postApprovalDecision({
        threadId,
        approvalId,
        action: "reject",
      });
      setStatus("rejected");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg || "提交失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <CardShell title="SQL 审批">
      <div className="flex flex-col gap-3">
        <div className="rounded-lg border bg-muted/30 p-3">
          <div className="text-xs leading-5 text-muted-foreground">
            <span className="font-medium text-foreground">只读执行</span>
            {readOnly ? "（read-only）" : ""}
            {maxRows ? ` · max_rows=${maxRows}` : ""}
            <span className="ml-2">执行前必须由你批准。</span>
          </div>
          {reason ? (
            <div className="mt-2 text-sm leading-6 text-foreground/90">
              {reason}
            </div>
          ) : null}
        </div>

        <div className="rounded-lg border bg-background p-2">
          {mode === "edit" ? (
            <Textarea
              value={sqlDraft}
              onChange={(e) => setSqlDraft(e.target.value)}
              className="min-h-28 font-mono text-xs"
              disabled={!canAct}
            />
          ) : (
            <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5">
              {proposedSql || "(empty sql)"}
            </pre>
          )}
        </div>

        {error ? (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={handleApprove}
            disabled={!canAct}
          >
            {submitting ? "提交中…" : "Approve"}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setMode((m) => (m === "edit" ? "view" : "edit"))}
            disabled={!canAct}
          >
            {mode === "edit" ? "Cancel" : "Edit"}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={handleReject}
            disabled={!canAct}
          >
            Reject
          </Button>

          {status !== "idle" ? (
            <span
              className={cn(
                "ml-auto rounded-full border px-2 py-0.5 text-xs",
                status === "approved"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-amber-200 bg-amber-50 text-amber-700",
              )}
            >
              {status === "approved" ? "已批准" : "已拒绝"}
            </span>
          ) : null}
        </div>
      </div>
    </CardShell>
  );
}

function ResultTableCard({ ui }: { ui: UIMessage }) {
  const props = isRecord(ui.props) ? ui.props : {};
  const columns = Array.isArray(props.columns)
    ? props.columns.filter((c): c is string => typeof c === "string")
    : [];
  const rowsAny = Array.isArray(props.rows) ? props.rows : [];
  const rows = rowsAny.filter((r) => Array.isArray(r)) as unknown[][];
  const rowCount =
    getNumber(props, "returned_row_count") ?? getNumber(props, "row_count");
  const durationMs = getNumber(props, "duration_ms");
  const truncated = getBoolean(props, "truncated") ?? false;
  const sql = getString(props, "sql") ?? "";

  const preview = buildTablePreview({ columns, rows, maxRows: 20, maxCols: 12 });

  const durationText = formatDuration(durationMs);
  const countText = rowCount != null ? `${rowCount} row(s)` : null;

  return (
    <CardShell title="查询结果">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {countText ? <span>{countText}</span> : null}
          {durationText ? <span>· {durationText}</span> : null}
          {truncated ? (
            <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-amber-700">
              truncated
            </span>
          ) : null}
        </div>

        {sql ? (
          <div className="rounded-lg border bg-background p-2">
            <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5">
              {clipSql(sql)}
            </pre>
          </div>
        ) : null}

        <div className="rounded-lg border bg-background p-2">
          <pre className="max-h-64 overflow-auto whitespace-pre font-mono text-xs leading-5">
            {preview.text}
          </pre>
        </div>

        {preview.hiddenRows > 0 || preview.hiddenCols > 0 ? (
          <div className="text-xs text-muted-foreground">
            仅预览前 {Math.min(rows.length, 20)} 行、前 {Math.min(columns.length, 12)} 列；
            {preview.hiddenRows > 0 ? `隐藏 ${preview.hiddenRows} 行` : ""}
            {preview.hiddenRows > 0 && preview.hiddenCols > 0 ? "，" : ""}
            {preview.hiddenCols > 0 ? `隐藏 ${preview.hiddenCols} 列` : ""}
            。
          </div>
        ) : null}
      </div>
    </CardShell>
  );
}
