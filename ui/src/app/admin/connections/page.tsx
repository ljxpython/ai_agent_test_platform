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

type ConnectionRow = {
	id?: string;
	kind?: string;
	config_json?: {
		sqlite_path?: string;
		readonly?: boolean;
		[key: string]: unknown;
	};
	created_at?: string;
};

const SKELETON_ROW_KEYS = ["1", "2", "3", "4", "5", "6"];

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

async function fetchConnections(
	signal?: AbortSignal,
): Promise<ConnectionRow[]> {
	const res = await fetch("/api/platform/connections", {
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
	if (Array.isArray(data)) return data as ConnectionRow[];
	if (isRecord(data) && Array.isArray(data.items)) {
		return data.items as ConnectionRow[];
	}
	return [];
}

export default function AdminConnectionsPage(): ReactNode {
	const [connections, setConnections] = useState<ConnectionRow[]>([]);
	const [isLoading, setIsLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);

	const identity = useMemo(() => {
		const h = getIdentityHeaders();
		return { userId: h["X-User-Id"], projectId: h["X-Project-Id"] };
	}, []);

	type LoadResult = (() => void) | undefined;
	const load = useCallback((): LoadResult => {
		const controller = new AbortController();
		setIsLoading(true);
		setError(null);

		fetchConnections(controller.signal)
			.then((rows) => {
				setConnections(rows);
				setLastUpdatedAt(new Date());
			})
			.catch((e) => {
				if (isAbortError(e)) return;
				setError(e instanceof Error ? e.message : "Unknown error");
			})
			.finally(() => setIsLoading(false));

		return () => controller.abort();
	}, []);

	useEffect(() => {
		const cleanup = load();
		return () => cleanup?.();
	}, [load]);

	return (
		<div className="mx-auto w-full max-w-5xl p-6">
			<div className="mb-4 flex flex-wrap items-end justify-between gap-3">
				<div>
					<h1 className="text-2xl font-semibold">Admin / Connections</h1>
					<p className="text-muted-foreground text-sm">
						Project <code className="font-mono">{identity.projectId}</code>,
						user <code className="font-mono">{identity.userId}</code>
					</p>
				</div>

				<div className="flex items-center gap-2">
					<Button asChild variant="outline">
						<Link href="/admin/audit">Audit</Link>
					</Button>
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
					<CardTitle>Connections</CardTitle>
					<CardDescription>
						Read-only. Last updated:{" "}
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
									<th className="px-3 py-2 text-left font-medium">Kind</th>
									<th className="px-3 py-2 text-left font-medium">
										sqlite_path
									</th>
									<th className="px-3 py-2 text-left font-medium">readonly</th>
									<th className="px-3 py-2 text-left font-medium">Created</th>
								</tr>
							</thead>
							<tbody className="divide-y">
								{isLoading ? (
									SKELETON_ROW_KEYS.map((k) => (
										<tr key={`sk:${k}`}>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-20" />
											</td>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-[520px]" />
											</td>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-16" />
											</td>
											<td className="px-3 py-2">
												<Skeleton className="h-4 w-40" />
											</td>
										</tr>
									))
								) : connections.length ? (
									connections.map((c) => {
										const kind = c.kind ?? "-";
										const sqlitePath = c.config_json?.sqlite_path ?? "-";
										const readonly =
											typeof c.config_json?.readonly === "boolean"
												? c.config_json.readonly
												: null;
										const created = formatDateTime(c.created_at);
										const key = c.id ?? `${kind}:${c.created_at ?? "-"}`;

										return (
											<tr key={key} className="align-top">
												<td className="px-3 py-2">
													<span className="font-mono text-xs">{kind}</span>
												</td>
												<td className="px-3 py-2" title={sqlitePath}>
													<span className="font-mono text-xs">
														{sqlitePath}
													</span>
												</td>
												<td className="px-3 py-2">
													<span className="font-mono text-xs">
														{readonly == null
															? "-"
															: readonly
																? "true"
																: "false"}
													</span>
												</td>
												<td
													className="px-3 py-2 whitespace-nowrap"
													title={c.created_at ?? ""}
												>
													{created}
												</td>
											</tr>
										);
									})
								) : (
									<tr>
										<td
											className="px-3 py-10 text-center text-muted-foreground"
											colSpan={4}
										>
											No connections found.
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
