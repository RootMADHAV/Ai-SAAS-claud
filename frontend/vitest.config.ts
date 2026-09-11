import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // tsconfig.json sets "jsx": "preserve" for Next.js's own SWC compiler
  // to handle. Vite's default transformer reads that same tsconfig and
  // fails to parse .tsx files as a result (tried overriding esbuild's
  // own jsx option first, but Vite 8's default oxc-based transformer
  // ignores that override) -- @vitejs/plugin-react takes over the
  // JSX/TSX transform entirely and sidesteps the issue. Test-runner-only;
  // the shared tsconfig, and Next's own build, are untouched.
  plugins: [react()],
  resolve: {
    // Vite/Vitest does not read tsconfig.json's "paths" automatically --
    // this mirrors that mapping (@/* -> ./src/*) for the module
    // resolver Next.js's own bundler already applies at build time.
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    // Default environment: most tests (the API client) have no DOM
    // dependency, just global fetch() -- the lighter "node" environment
    // is enough for those. Files that need a DOM (React component tests,
    // e.g. lib/auth/context.test.tsx) opt in per-file via a
    // '// @vitest-environment jsdom' docblock rather than paying the
    // jsdom cost project-wide.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    // Fixed value so tests never depend on a local .env.local existing
    // (that file is gitignored/per-developer -- see
    // .env.local.example). Matches the locked mkcert local-HTTPS setup
    // in shape (https://, port 8000) without being a real network
    // target -- every test mocks global fetch rather than hitting it.
    env: {
      NEXT_PUBLIC_API_BASE_URL: "https://localhost:8000",
    },
  },
});
