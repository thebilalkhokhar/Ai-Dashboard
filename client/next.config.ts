import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  allowedDevOrigins: ["127.0.0.1"],
  // Reviewed: `experimental.agentFeedback` was removed - it only makes
  // `next dev` write its feedback block into AGENTS.md and no code needs it.
  // `cacheComponents` stays because `partialPrefetching` requires it (the
  // build fails without it); nothing in the app uses 'use cache' yet.
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
