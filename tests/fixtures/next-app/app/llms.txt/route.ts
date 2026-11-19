export function GET() {
  return new Response("# Acme\n", { headers: { "content-type": "text/markdown" } });
}
