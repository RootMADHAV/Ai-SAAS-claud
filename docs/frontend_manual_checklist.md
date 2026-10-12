# Frontend manual checklist — authentication to scan workflow

Repeatable browser walkthrough of the one workflow the frontend currently
supports: **register → log in → create an organization → start a scan →
run it → watch the steps → log out**. Automated tests (`npm test`) cover
components and the API client with mocked `fetch`; they do **not** cover
real cookies, CORS, the backend, the Celery worker or a real scanner.
This checklist does.

Record the date, git commit (`git rev-parse --short HEAD`), browser and
result next to each run. A box is only ticked from what you actually saw.

## Scope (what is NOT here)

No scan history, findings, organization switching or member management
exists in the UI yet. If a step below needs one of those, it is out of
scope — do not add it to this checklist until the feature exists.

## 0. Preconditions

- [ ] Local HTTPS dev set up per `frontend/README.md` (mkcert certs;
      `Secure` auth cookies are only stored over HTTPS, so frontend **and**
      backend must both be HTTPS).
- [ ] Backend running over HTTPS on `https://localhost:8000` with
      `CORS_ALLOWED_ORIGINS=https://localhost:3000` (see `.env.example`),
      database migrated, Redis and MinIO reachable.
- [ ] A Celery worker consuming the `scans` queue is running
      (`celery -A app.workers.celery_app worker -Q scans`, as in
      `docker-compose.yml`) with scanner binaries (nuclei/nmap) and
      `ANTHROPIC_API_KEY` available to it. Without a worker the scan will
      not progress past the point where it is handed to the queue.
- [ ] Frontend running: from `frontend/`, `npm run dev` →
      `https://localhost:3000`. `frontend/.env.local` points at
      `https://localhost:8000`.
- [ ] Use a fresh browser profile or clear site data for `localhost`
      (cookies **and** localStorage) before starting.
- [ ] Have a scan target you are **authorized** to scan and that is
      publicly routable. The backend rejects private/loopback addresses
      (SSRF protection), so `localhost`/`192.168.x.x` targets are expected
      to be refused — see check 6c.

## 1. Unauthenticated access

- [ ] Open `https://localhost:3000/dashboard` → briefly "Loading…", then
      redirected to `/login`. No dashboard content flashes.

## 2. Register

- [ ] Open `/register`, submit a new email, a password and a full name →
      account created and you land in the authenticated app (nav bar shows
      the logged-in state).
- [ ] Repeat with the same email (log out first) → a readable error
      message, no crash, no redirect.

## 3. Session restore and logout

- [ ] Reload the page while logged in → stays logged in (no login
      screen), via the silent refresh.
- [ ] DevTools → Application → Cookies: the auth cookies are `HttpOnly`,
      `Secure`; nothing token-like in localStorage/sessionStorage.
- [ ] Log out → returned to the logged-out state; `/dashboard` redirects
      to `/login` again; the cookies are cleared.
- [ ] Log in again at `/login` with the account from step 2 → success.
- [ ] Wrong password → readable error, stays on `/login`.

## 4. Organization

- [ ] First visit to `/dashboard` with nothing stored → "Create an
      organization" form (not the scan form).
- [ ] Submit a name and a slug (lowercase letters, numbers, hyphens) →
      the page switches to the "New scan" form without a reload.
- [ ] Reload → still the "New scan" form (the selected organization id is
      read back from localStorage key
      `security-platform:selected-organization-id`).
- [ ] Invalid slug (e.g. `Bad Slug`) → blocked by the form pattern.
- [ ] Duplicate slug → readable error (conflict), form stays usable.

## 5. Start a scan

- [ ] The "New scan" form shows a Target field and the fixed label
      "Scanner: nuclei (fixed for this MVP)". (The scanner is not
      selectable in this milestone.)
- [ ] Submit an authorized public target → button shows "Starting…" while
      in flight, then you are taken to `/scans/<id>`.
- [ ] Scan page shows `Scan: <target>`, `Status: queued`,
      `Scanner: nuclei`, and a **Run scan** button.

## 6. Run the scan and read the steps

- [ ] Click **Run scan** → status moves to `running`; the button is
      replaced by "Scan is running…"; the page updates on its own
      (polling roughly every 3 s) without reloading.
- [ ] The workflow steps list shows step names and statuses in order.
      For each step that has them you also see `Started:` /
      `Completed:` timestamps (`YYYY-MM-DD HH:MM:SS UTC`).
- [ ] When the scan finishes, status becomes `completed`, polling stops
      (no further requests in the Network tab) and the Run/Retry button is
      shown.
- [ ] **6c. Failure path** — use a target the backend will refuse (e.g. a
      private address). Where the refusal happens (at creation, shown as a
      red message under the form, or when the scan runs) is not verified —
      record which you see. If the scan is created and runs: status becomes
      `failed`; the failing step shows its status, any `Retries: N` (only
      when N > 0) and an `Error: …` line with the backend's message; a
      **Retry scan** button is offered. Click it → status returns to
      `running`.
- [ ] Reload `/scans/<id>` mid-run and after completion → page restores
      from the API with the same status and steps.
- [ ] Navigate to `/scans/<id>` for an id that does not exist → readable
      error with a "Back to dashboard" link.

## 7. Session expiry during a scan (optional)

- [ ] With the scan page open, delete only the access-token cookie in
      DevTools, wait for the next poll → the request is transparently
      retried after a silent refresh; the page keeps updating. If the
      refresh cookie is gone too, you are sent to `/login`.

## 8. Wrap-up

- [ ] DevTools Console: no unexpected errors during the run.
- [ ] Note any step that behaved differently from this document and fix
      the **document or the code** — do not leave the mismatch.

## Maintenance

Update this file in the same change as any UI change to the workflow
above. Last reviewed against: Phase 7 M1 (written without being executed
against a live stack — the first real run should confirm every
"expected" statement here).
