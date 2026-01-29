"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

type RunAuditRow = {
	id?: string;
	project_id?: string;
	user_id?: string | null;
	thread_id?: string | null;
	run_id?: string;
	parent_run_id?: string | null;
	status?: string;
	sql_text?: string | null;
	row_count?: number | null;
	duration_ms?: number | null;
	error_summary?: string | null;
	created_at?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
	return !!value && typeof value === "object";
}

function isAbortError(value: unknown): boolean {
	return (
		isRecord(value) &&
		"name" in value &&
		typeof (value as { name?: unknown }).name === "string" &&
		(value as { name: string }).name === "AbortError"
	);
}

function getIdentityHeaders(): Record<string, string> {
	// Keep defaults consistent with ui/src/providers/client.ts
	const userId = process.env.NEXT_PUBLIC_USER_ID ?? "demo";
	const projectId = process.env.NEXT_PUBLIC_PROJECT_ID ?? "default";
	return {
		"X-User-Id": userId,
		"X-Project-Id": projectId,
	};
}

function formatDateTime(value: string | undefined): string {
	if (!value) return "-";
	const d = new Date(value);
	if (Number.isNaN(d.getTime())) return value;
	return d.toLocaleString();
}

function statusPillClass(status: string | undefined): string {
	switch ((status ?? "").toLowerCase()) {
		case "succeeded":
			return "bg-emerald-50 text-emerald-700 border-emerald-200";
		case "failed":
			return "bg-rose-50 text-rose-700 border-rose-200";
		case "started":
			return "bg-sky-50 text-sky-700 border-sky-200";
		default:
			return "bg-muted text-muted-foreground border-border";
	}
}

async function fetchRun(
	runId: string,
	signal?: AbortSignal,
): Promise<RunAuditRow> {
	const res = await fetch(`/api/platform/runs/${encodeURIComponent(runId)}`, {
		method: "GET",
		headers: {
			Accept: "application/json",
			...getIdentityHeaders(),
		},
		...(signal ? { signal } : {}),
	});

	if (res.status === 404) {
		throw new Error("NOT_FOUND");
	}
	if (!res.ok) {
		const text = await res.text().catch(() => "");
		const hint = text ? `: ${text}` : "";
		throw new Error(`Request failed (${res.status})${hint}`);
	}

	const data: unknown = await res.json();
	if (isRecord(data)) return data as RunAuditRow;
	return {};
}

export default function AdminAuditRunPage(): ReactNode {
	const params = useParams<{ runId?: string | string[] }>();
	const runIdParam = params?.runId;
	const runId =
		typeof runIdParam === "string"
			? runIdParam
			: Array.isArray(runIdParam)
				? (runIdParam[0] ?? "")
				: "";

	const [isLoading, setIsLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [notFound, setNotFound] = useState(false);
	const [run, setRun] = useState<RunAuditRow | null>(null);

	const load = useCallback(() => {
		if (!runId) return;
		const controller = new AbortController();

		setIsLoading(true);
		setError(null);
		setNotFound(false);

		fetchRun(runId, controller.signal)
			.then((row) => {
				setRun(row);
			})
			.catch((e) => {
				if (isAbortError(e)) return;
				if (e instanceof Error && e.message === "NOT_FOUND") {
					setNotFound(true);
					setRun(null);
					return;
				}
				setError(e instanceof Error ? e.message : "Unknown error");
			})
			.finally(() => setIsLoading(false));

		return () => controller.abort();
	}, [runId]);

	useEffect(() => {
		const cleanup = load();
		return () => cleanup?.();
	}, [load]);

	return (
		<div className="mx-auto w-full max-w-4xl p-6">
			<div className="mb-4 flex items-center justify-between gap-3">
				<Button asChild variant="outline">
					<Link href="/admin/audit">Back</Link>
				</Button>

				<Button
					type="button"
					variant="outline"
					onClick={() => void load()}
					disabled={!runId || isLoading}
				>
					Refresh
				</Button>
			</div>

			{notFound ? (
				<Card>
					<CardHeader>
						<CardTitle>Run not found</CardTitle>
						<CardDescription>
							No run exists for <code className="font-mono">{runId}</code>.
						</CardDescription>
					</CardHeader>
				</Card>
			) : (
				<Card>
					<CardHeader>
						<CardTitle className="flex flex-wrap items-center gap-2">
							<span>Run</span>
							{isLoading ? (
								<Skeleton className="h-6 w-56" />
							) : (
								<code className="font-mono text-sm">{runId || "-"}</code>
							)}
							{isLoading ? (
								<Skeleton className="h-5 w-20" />
							) : (
								<span
									className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${statusPillClass(run?.status)}`}
								>
									{run?.status ?? "unknown"}
								</span>
							)}
						</CardTitle>
						<CardDescription>
							{isLoading ? (
								<Skeleton className="h-4 w-72" />
							) : (
								<span>Created {formatDateTime(run?.created_at)}</span>
							)}
						</CardDescription>
					</CardHeader>
					<CardContent>
						{error ? (
							<div className="mb-4 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
								{error}
							</div>
						) : null}

						<div className="grid grid-cols-1 gap-3 md:grid-cols-2">
							<div className="rounded-lg border p-3">
								<div className="text-muted-foreground text-xs">Thread ID</div>
								<div className="mt-1 font-mono text-xs">
									{isLoading ? (
										<Skeleton className="h-4 w-40" />
									) : (
										(run?.thread_id ?? "-")
									)}
								</div>
							</div>

							<div className="rounded-lg border p-3">
								<div className="text-muted-foreground text-xs">
									Parent Run ID
								</div>
								<div className="mt-1 font-mono text-xs">
									{isLoading ? (
										<Skeleton className="h-4 w-40" />
									) : run?.parent_run_id ? (
										run.parent_run_id
									) : (
										"-"
									)}
								</div>
							</div>

							<div className="rounded-lg border p-3">
								<div className="text-muted-foreground text-xs">Row Count</div>
								<div className="mt-1 tabular-nums">
									{isLoading ? (
										<Skeleton className="h-4 w-20" />
									) : (
										(run?.row_count ?? "-")
									)}
								</div>
							</div>

							<div className="rounded-lg border p-3">
								<div className="text-muted-foreground text-xs">Duration</div>
								<div className="mt-1 tabular-nums">
									{isLoading ? (
										<Skeleton className="h-4 w-24" />
									) : run?.duration_ms != null ? (
										`${run.duration_ms} ms`
									) : (
										"-"
									)}
								</div>
							</div>
						</div>

						<div className="mt-4">
							<div className="mb-2 text-sm font-medium">SQL</div>
							{isLoading ? (
								<div className="space-y-2">
									<Skeleton className="h-4 w-full" />
									<Skeleton className="h-4 w-11/12" />
									<Skeleton className="h-4 w-4/5" />
								</div>
							) : (
								<pre className="max-w-full overflow-x-auto whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 font-mono text-xs">
									{run?.sql_text?.trim() ? run.sql_text : "-"}
								</pre>
							)}
						</div>

						{!isLoading && run?.error_summary ? (
							<div className="mt-4">
								<div className="mb-2 text-sm font-medium">Error</div>
								<div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
									{run.error_summary}
								</div>
							</div>
						) : null}
					</CardContent>
				</Card>
			)}
		</div>
	);
}
