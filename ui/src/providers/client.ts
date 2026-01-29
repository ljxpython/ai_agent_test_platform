import { Client } from "@langchain/langgraph-sdk";

export function createClient(apiUrl: string, apiKey: string | undefined) {
  const userId = process.env.NEXT_PUBLIC_USER_ID ?? "demo";
  const projectId = process.env.NEXT_PUBLIC_PROJECT_ID ?? "default";

  return new Client({
    apiKey,
    apiUrl,
    defaultHeaders: {
      "X-User-Id": userId,
      "X-Project-Id": projectId,
    },
  });
}
