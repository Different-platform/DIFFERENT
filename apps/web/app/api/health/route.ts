// Server-side proxy for the backend health check.
//
// Same secrets boundary as the chat proxy: the browser asks this route, which
// forwards to the backend with the shared secret. Used by the UI to show
// which survey years are available and whether the LLM is live or stubbed.

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const API_SHARED_SECRET = process.env.API_SHARED_SECRET ?? "";

export async function GET() {
  const headers: Record<string, string> = {};
  if (API_SHARED_SECRET) headers["x-api-secret"] = API_SHARED_SECRET;

  try {
    const upstream = await fetch(`${API_BASE_URL}/api/health`, { headers });
    const data = await upstream.json();
    return new Response(JSON.stringify(data), {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    return new Response(
      JSON.stringify({ status: "unreachable", error: String(err) }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }
}
