"use client";

import Link from "next/link";
import {
	type ReactNode,
	useCallback,
	useEffect,
	useMemo,
	useState,
} from "react";
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

const SKELETON_ROW_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8"];

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

function truncateOneLine(value: string | null | undefined, max = 120): string {
	const v = (value ?? "").replace(/\s+/g, " ").trim();
	if (!v) return "-";
	if (v.length <= max) return v;
	return `${v.slice(0, max)}...`;
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

async function fetchRuns(signal?: AbortSignal): Promise<RunAuditRow[]> {
	const res = await fetch("/api/platform/runs?limit=50", {
		method: "GET",
		headers: {
			Accept: "application/json",
			...getIdentityHeaders(),
		},
		...(signal ? { signal } : {}),
	});

	if (!res.ok) {
		const text = await res.text().catch(() => "");
		const hint = text ? `: ${text}` : "";
		throw new Error(`Request failed (${res.status})${hint}`);
	}

	const data: unknown = await res.json();
	if (Array.isArray(data)) return data as RunAuditRow[];
	if (isRecord(data) && Array.isArray(data.items)) {
		return data.items as RunAuditRow[];
	}
	return [];
}

export default function AdminAuditPage(): ReactNode {
	const [runs, setRuns] = useState<RunAuditRow[]>([]);
	const [isLoading, setIsLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);

	const identity = useMemo(() => {
		const h = getIdentityHeaders();
		return { userId: h["X-User-Id"], projectId: h["X-Project-Id"] };
	}, []);

	const load = useCallback(async () => {
		setIsLoading(true);
		setError(null);
		try {
			const rows = await fetchRuns();
			setRuns(rows);
			setLastUpdatedAt(new Date());
		} catch (e) {
			setError(e instanceof Error ? e.message : "Unknown error");
		} finally {
			setIsLoading(false);
		}
	}, []);

	useEffect(() => {
		const controller = new AbortController();
		setIsLoading(true);
		setError(null);
		fetchRuns(controller.signal)
			.then((rows) => {
				setRuns(rows);
				setLastUpdatedAt(new Date());
			})
			.catch((e) => {
				if (isAbortError(e)) return;
				setError(e instanceof Error ? e.message : "Unknown error");
			})
			.finally(() => setIsLoading(false));

		return () => controller.abort();
	}, []);

	return (
		<div className="mx-auto w-full max-w-6xl p-6">
			<div className="mb-4 flex flex-wrap items-end justify-between gap-3">
				<div>
					<h1 className="text-2xl font-semibold">Admin / Audit</h1>
					<p className="text-muted-foreground text-sm">
						Project <code className="font-mono">{identity.projectId}</code>,
						user <code className="font-mono">{identity.userId}</code>
					</p>
				</div>

				<div className="flex items-center gap-2">
					<Button
						type="button"
						variant="outline"
						onClick={() => void load()}
						disabled={isLoading}
					>
						Refresh
					</Button>
				</div>
			</div>

			<Card>
				<CardHeader>
					<CardTitle>Runs</CardTitle>
					<CardDescription>
						Latest 50 runs. Last updated:{" "}
						{lastUpdatedAt ? lastUpdatedAt.toLocaleString() : "-"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					{error ? (
						<div className="space-y-3">
							<div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
								{error}
							</div>
							<Button
								type="button"
								variant="outline"
								onClick={() => void load()}
							>
								Retry
							</Button>
						</div>
					) : null}

					<div className="max-w-full overflow-x-auto rounded-lg border">
						<table className="w-full border-collapse text-sm">
							<thead>
								<tr className="bg-muted text-muted-foreground">
									<th className="px-3 py-2 text-left font-medium">Created</th>
									<th className="px-3 py-2 text-left font-medium">Status</th>
									<th className="px-3 py-2 text-left font-medium">Run ID</th>
									<th className="px-3 py-2 text-left font-medium">Thread ID</th>
									<th className="px-3 py-2 text-left font-medium">SQL</th>
									<th className="px-3 py-2 text-right font-medium">Rows</th>
									<th className="px-3 py-2 text-right font-medium">Duration</th>
								</tr>
							</thead>
							<tbody className="divide-y">
								{isLoading ? (
									SKELETON_ROW_KEYS.map((k) => (
										// biome-ignore lint/suspicious/noArrayIndexKey: stable skeleton row keys
										<tr key={`sk:${k}`}>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-32" />
											</td>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-20" />
											</td>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-52" />
											</td>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-44" />
											</td>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-[520px]" />
											</td>
											<td className="px-3 py-2 text-right">
												<Skeleton className="ml-auto h-4 w-12" />
											</td>
											<td className="px-3 py-2 text-right">
												<Skeleton className="ml-auto h-4 w-16" />
											</td>
										</tr>
									))
								) : runs.length ? (
									runs.map((r) => {
										const runId = r.run_id ?? "";
										const threadId = r.thread_id ?? "";
										const sql = truncateOneLine(r.sql_text, 140);
										const created = formatDateTime(r.created_at);
										const status = r.status ?? "unknown";
										const rowCount = r.row_count ?? null;
										const durationMs = r.duration_ms ?? null;
										const key =
											r.id ??
											r.run_id ??
											`${r.thread_id ?? "-"}:${r.created_at ?? "-"}`;

										return (
											<tr key={key} className="align-top">
												<td
													className="px-3 py-2 whitespace-nowrap"
													title={r.created_at ?? ""}
												>
													{created}
												</td>
												<td className="px-3 py-2">
													<span
														className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${statusPillClass(status)}`}
														title={r.error_summary ?? ""}
													>
														{status}
													</span>
												</td>
												<td className="px-3 py-2">
													{runId ? (
														<Link
															href={`/admin/audit/${encodeURIComponent(runId)}`}
															className="font-mono text-xs text-primary underline-offset-4 hover:underline"
														>
															{runId}
														</Link>
													) : (
														<span className="text-muted-foreground">-</span>
													)}
												</td>
												<td className="px-3 py-2">
													{threadId ? (
														<span className="font-mono text-xs">
															{threadId}
														</span>
													) : (
														<span className="text-muted-foreground">-</span>
													)}
												</td>
												<td className="px-3 py-2" title={r.sql_text ?? ""}>
													<span className="font-mono text-xs text-foreground/90">
														{sql}
													</span>
												</td>
												<td className="px-3 py-2 text-right tabular-nums">
													{rowCount ?? "-"}
												</td>
												<td className="px-3 py-2 text-right tabular-nums">
													{durationMs != null ? `${durationMs} ms` : "-"}
												</td>
											</tr>
										);
									})
								) : (
									<tr>
										<td
											className="px-3 py-10 text-center text-muted-foreground"
											colSpan={7}
										>
											No runs found.
										</td>
									</tr>
								)}
							</tbody>
						</table>
					</div>
				</CardContent>
			</Card>
		</div>
	);
}
