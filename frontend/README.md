# Security Platform — Frontend

Next.js (App Router) + TypeScript + Tailwind CSS, scaffolded for the
Phase 3 MVP frontend. See the repository root `PROJECT_STATE.md` for the
full architecture/decisions reference this frontend follows.

## Why local dev runs over HTTPS

The backend's auth cookies are always set with the `Secure` attribute —
this is a locked, unconditional decision (`PROJECT_STATE.md` §4), never
relaxed for local convenience. A `Secure` cookie is only *stored* by the
browser if the response that set it arrived over HTTPS, regardless of
what scheme the page making the request is served over. That means
**both** the frontend dev server *and* the backend dev server need to
run over HTTPS locally, or `/auth/login`'s `Set-Cookie` will be silently
dropped by the browser and every subsequent authenticated request will
401. This is a one-time local setup cost — no application code (frontend
or backend) changes as a result.

## One-time setup

1. Install [`mkcert`](https://github.com/FiloSottile/mkcert) and
   register a local CA (`mkcert -install`).
2. From `frontend/`, generate a certificate covering both dev ports
   (they're the same host, `localhost`, just different ports):

   ```bash
   mkcert -key-file certs/localhost-key.pem -cert-file certs/localhost.pem \
     localhost 127.0.0.1 ::1
   ```

   `certs/` is gitignored — every developer generates their own local
   certificate; nothing under it is ever committed.
3. `cp .env.local.example .env.local` (already points at
   `https://localhost:8000`; adjust only if your backend port differs).
4. `npm install --legacy-peer-deps`

   The `--legacy-peer-deps` flag works around a known npm bug (not a
   real dependency conflict of ours) in how npm's resolver walks
   Vitest 4/Vite 8's optional-peer graph on a clean install
   (`Cannot read properties of null (reading 'edgesOut')` --
   reproduces even with `@vitejs/plugin-react` removed, so it's not
   something this project's own choices introduced). A plain
   `npm install` will fail with that error; this flag is required, not
   optional, until upstream fixes it.

## Running locally

**Backend** (from `backend/`, reusing the same certs so both processes
trust the same local CA):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --ssl-keyfile ../frontend/certs/localhost-key.pem \
  --ssl-certfile ../frontend/certs/localhost.pem
```

Also set `CORS_ALLOWED_ORIGINS=https://localhost:3000` in the backend's
`.env` (root `.env.example` documents this — note the `https://`, which
only applies to this local-HTTPS setup).

**Frontend:**

```bash
npm run dev
```

Serves `https://localhost:3000` (self-signed via the mkcert cert
generated above — `next dev --experimental-https` picks it up via the
`certs/localhost*.pem` paths already wired into `package.json`'s `dev`
script).

## Scripts

- `npm run dev` — dev server, HTTPS (see above)
- `npm run build` / `npm run start` — production build/serve
- `npm run lint` — ESLint (`next/core-web-vitals`, invoked directly
  rather than via the now-deprecated `next lint` wrapper)
- `npm run typecheck` — `tsc --noEmit`
- `npm run test` — Vitest (node environment by default; component
  tests opt into jsdom per-file via a `// @vitest-environment jsdom`
  docblock)

## Structure

```
src/app/          App Router routes: /, /login, /register, /dashboard
                  (protected placeholder)
src/components/   Shared UI components (nav-bar.tsx; components.json is
                  configured for the shadcn CLI for anything added later)
src/lib/api/      Typed API client foundation (Step 2) -- one function
                  per backend endpoint, a typed error hierarchy, the
                  401-silent-refresh-retry-once core
src/lib/auth/     Client-side session state (Step 3) -- AuthProvider/
                  useAuth (mirrors the backend's cookie-based session,
                  never reads/stores a token itself) and RequireAuth
                  (client-side route guard, not a security boundary --
                  see that file's own docstring)
```

## Dependency notes

- **Next.js 15 (15.5.25), not 14 or 16.** The initial 14.2.15 pin had
  4 high + 1 critical CVE per `npm audit`; 15.5.25 (latest 15.x)
  resolves all of them while staying on React 18 (Next 15 supports it
  as a peer dependency, so no forced React 19 migration). Next 16 is
  npm's current `latest` tag and is what `npm audit fix --force` wants,
  but it's a major version bump not evaluated here — see Technical Debt
  below.
- **One residual advisory, accepted for now:** a high-severity PostCSS
  issue (XSS/path traversal via CSS source maps) is bundled inside
  Next 15's own internal build tooling
  (`node_modules/next/node_modules/postcss`), not our own top-level
  `postcss` (already bumped to latest). Only resolved by the Next 16
  jump. Low practical exploitability for this project — the affected
  code path processes untrusted CSS source-map input, which doesn't
  occur in our own build pipeline — but it's a real, disclosed,
  currently-unpatched-in-15.x issue, not silently ignored.
- **`npm install` needs `--legacy-peer-deps`** — see step 4 above. A
  known npm resolver bug in the Vitest 4/Vite 8 optional-peer graph, not
  a real conflict between this project's own dependency choices.

## Status

Steps 1–3 of the Phase 3 MVP frontend plan are complete: scaffold,
typed API client, and the auth/session flow (register, login, session
restore on load, protected routes, logout, basic nav state). No
organization/scan UI yet — that's Step 4. See the root
`docs/session_state.md` for exact current progress and verification
results.
