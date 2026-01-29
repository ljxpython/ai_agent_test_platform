import { expect, test } from "@playwright/test";

function parseRunIdFromContentLocation(value: string): string {
  // Content-Location 应指向创建的 run 资源，例如：
  //   /runs/<run_id>
  //   /threads/<thread_id>/runs/<run_id>
  //   https://host/api/lg/.../runs/<run_id>
  // 这里按 path 分段解析，避免在代理/不同 baseURL 下正则误判。
  const asUrl = value.startsWith("http://") || value.startsWith("https://")
    ? new URL(value)
    : new URL(value, "http://localhost");

  const segments = asUrl.pathname.split("/").filter(Boolean);
  const runsIdx = segments.lastIndexOf("runs");
  const runId = runsIdx >= 0 ? segments[runsIdx + 1] : undefined;
  if (!runId) {
    throw new Error(
      `Unable to parse run_id from Content-Location: ${JSON.stringify(value)}`,
    );
  }
  return runId;
}

test("SQL HITL flow: run -> approval -> execute -> result -> audit", async ({
  page,
  baseURL,
}) => {
  // Expect: system running via `docker compose -f infra/docker-compose.yml up -d --build`.
  // Use an absolute apiUrl so the LangGraph SDK can reliably build request URLs.
  const origin = new URL(baseURL ?? "http://localhost").origin;
  const apiUrl = new URL("/api/lg", origin).toString();

  const entry = new URL("/", origin);
  entry.searchParams.set("apiUrl", apiUrl);
  entry.searchParams.set("assistantId", "sql_agent");
  await page.goto(entry.toString());

  const messageBox = page.getByPlaceholder("Type your message...");
  await expect(messageBox).toBeVisible();

  await messageBox.fill("List 5 artists");
  // Ensure the message is actually sent (avoid flake where Send stays disabled).
  const send = page.getByRole("button", { name: "Send" });
  await expect(send).toBeEnabled();

  // LangGraph stream 创建 run 时应返回 Content-Location 指向该 run。
  // 本用例用 run_id 做审计断言（当前后端 thread_id 可能为 null）。
  const runCreateRes = await Promise.all([
    page.waitForResponse((res) => {
      const req = res.request();
      const { pathname } = new URL(res.url());
      return (
        req.method() === "POST" &&
        res.ok() &&
        // Allow both `/api/lg/runs/stream` and `/api/lg/<any...>/runs/stream`.
        pathname.includes("/api/lg/") && pathname.endsWith("/runs/stream")
      );
    }),
    send.click(),
  ]).then(([res]) => res);

  const contentLocation = runCreateRes.headers()["content-location"];
  if (!contentLocation) {
    // 需要明确失败：没有 run_id 会导致用例误断言，且难以排查。
    throw new Error(
      `Missing Content-Location header on run create response. Available headers: ${JSON.stringify(
        Object.keys(runCreateRes.headers()).sort(),
      )}`,
    );
  }
  const runId = parseRunIdFromContentLocation(contentLocation);

  const approvalTitle = page.getByText("SQL 审批", { exact: true });
  await expect(approvalTitle).toBeVisible();

  // The graph's default proposal is intentionally conservative; edit to a valid Chinook query.
  const approvalCard = approvalTitle.locator("..").locator("..");
  await approvalCard.getByRole("button", { name: "Edit" }).click();
  const approvedSql = "SELECT Name FROM Artist ORDER BY Name LIMIT 5";
  await approvalCard
    .getByRole("textbox")
    .fill(approvedSql);

  // 审批请求：/api/platform/threads/{threadId}/approvals/{approvalId}
  await Promise.all([
    page.waitForResponse((res) => {
      const req = res.request();
      return (
        req.method() === "POST" &&
        res.ok() &&
        /\/api\/platform\/threads\/.+\/approvals\/.+/.test(new URL(res.url()).pathname)
      );
    }),
    approvalCard.getByRole("button", { name: "Approve" }).click(),
  ]);
  await expect(approvalCard.getByText("已批准", { exact: true })).toBeVisible();

  await expect(page.getByText("查询结果", { exact: true })).toBeVisible();

  await page.goto("/admin/audit");
  await expect(page.getByRole("heading", { name: "Admin / Audit" })).toBeVisible();

  // Assert the run was audited. Prefer API-level polling over hard sleeps.
  // 备注：当前后端可能不会填充 thread_id（UI 会渲染为 "-"），所以用 run_id 做断言。
  const refresh = page.getByRole("button", { name: "Refresh" });
  await expect
    .poll(
      async () => {
        // 使用 UI 自身的 fetch（携带其鉴权头/上下文），避免 page.request 缺少鉴权导致 401。
        const [res] = await Promise.all([
          page.waitForResponse((r) => {
            const req = r.request();
            const url = new URL(r.url());
            return (
              req.method() === "GET" &&
              r.ok() &&
              url.pathname === "/api/platform/runs" &&
              // audit 页面默认请求：GET /api/platform/runs?limit=50
              url.searchParams.get("limit") === "50"
            );
          }),
          refresh.click(),
        ]);

        const data: unknown = await res.json().catch(() => null);
        const items = Array.isArray(data)
          ? (data as unknown[])
          : data && typeof data === "object" && Array.isArray((data as { items?: unknown }).items)
            ? ((data as { items: unknown[] }).items ?? [])
            : [];

        return items.some((row) => {
          if (!row || typeof row !== "object") return false;
          const auditedRunId = (row as { run_id?: unknown; runId?: unknown }).run_id ??
            (row as { run_id?: unknown; runId?: unknown }).runId;
          return typeof auditedRunId === "string" && auditedRunId === runId;
        });
      },
      {
        timeout: 90_000,
        intervals: [500, 1_000, 2_000, 3_000],
      },
    )
    .toBeTruthy();

  // API 已出现该 run 后，再通过 UI 刷新直到表格渲染出对应 run_id。
  await expect
    .poll(
      async () => {
        // 等待刷新请求返回后再断言 DOM，避免“点了刷新但表格还没渲染”的竞态。
        await Promise.all([
          page.waitForResponse((r) => {
            const req = r.request();
            const url = new URL(r.url());
            return (
              req.method() === "GET" &&
              r.ok() &&
              url.pathname === "/api/platform/runs" &&
              url.searchParams.get("limit") === "50"
            );
          }),
          refresh.click(),
        ]);

        // 某些 UI 组件可能不会把 td 的可访问名设为精确字符串；用文本匹配更稳。
        const cell = page.locator("table td").filter({ hasText: runId }).first();
        return cell.isVisible();
      },
      {
        timeout: 30_000,
        intervals: [500, 1_000, 2_000],
      },
    )
    .toBeTruthy();
});
