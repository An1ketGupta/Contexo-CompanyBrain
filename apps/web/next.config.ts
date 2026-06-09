import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  // Forward the upstream Request-ID on response so the browser can correlate
  // a failed UI action with backend logs. We also set this in the proxy
  // helper for each route, but adding it at the framework level catches
  // anything that bypasses the helper.
  async headers() {
    return [
      {
        source: "/api/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex" }],
      },
    ];
  },
};

const sentryOrg = process.env.SENTRY_ORG;
const sentryProject = process.env.SENTRY_PROJECT;
const sentryAuthToken = process.env.SENTRY_AUTH_TOKEN;

// withSentryConfig handles source-map upload, release tagging, tunneling, and
// instrumentation file detection. We only invoke it when the build-time creds
// are set so local `pnpm build` doesn't spam errors about missing tokens.
export default sentryOrg && sentryProject && sentryAuthToken
  ? withSentryConfig(nextConfig, {
      org: sentryOrg,
      project: sentryProject,
      authToken: sentryAuthToken,
      // Hide source maps from the public — they're uploaded to Sentry for
      // server-side symbolication only.
      sourcemaps: { deleteSourcemapsAfterUpload: true },
      // Silence the build logs in CI unless we explicitly opt in.
      silent: !process.env.CI,
      // Tunnels Sentry requests through /monitoring to dodge ad-blockers that
      // false-positive on sentry.io. Optional but standard practice now.
      tunnelRoute: "/monitoring",
      disableLogger: true,
    })
  : nextConfig;
