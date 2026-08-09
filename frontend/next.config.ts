import type { NextConfig } from "next";

/**
 * The browser never talks to the API directly — it calls this origin at `/api/*`
 * and Next proxies server-side to the backend.
 *
 * That is what makes the refresh-token cookie same-origin, so `SameSite=Lax`
 * works over plain HTTP in development. Talking to the backend cross-origin would
 * force `SameSite=None; Secure`, which requires HTTPS locally.
 */
const backendUrl = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
