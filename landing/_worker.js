/**
 * Cloudflare Pages Advanced Mode worker.
 * Handles SPA routing for /demo/:novelSlug/* routes.
 *
 * Problem: env.ASSETS.fetch('/demo/index.html') triggers Pretty URLs 308 redirect
 * to /demo/, losing the original URL and query params.
 * Solution: Fetch /demo/ directly (serves index.html without redirect), then
 * return the response body with the original request URL preserved.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Demo SPA routes: /demo/honglou/*, /demo/xiyouji/*
    if (/^\/demo\/(honglou|xiyouji)(\/|$)/.test(url.pathname)) {
      // Fetch /demo/ (trailing slash) to get index.html content without 308
      const assetResponse = await env.ASSETS.fetch(
        new Request(new URL('/demo/', url.origin), { headers: request.headers })
      );
      // Return the HTML body but keep the original URL (no redirect)
      return new Response(assetResponse.body, {
        status: 200,
        headers: assetResponse.headers,
      });
    }

    // Everything else: serve static files normally
    return env.ASSETS.fetch(request);
  }
};
