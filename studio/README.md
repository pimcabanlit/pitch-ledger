# Pitch Ledger Studio

Next.js (App Router, TypeScript strict, Tailwind, shadcn/ui-style components, Lucide icons).

- **Fan view** (`/matches/[id]`): aired insights only, by audience mode, language and club; refreshes every 5 s (Web PubSub push comes next).
- **Producer console** (`/producer`): the insight queue with status filters, each insight's audit trail, metrics and evidence events, and Approve / Block.

The browser calls `/api/v1/*` on the Studio's own origin; `next.config.ts` forwards it to the Python API (`BACKEND_URL`, default `http://127.0.0.1:8000`).

```
# terminal 1 (repo root): the API, with a local stand-in producer
PITCH_LEDGER_API_AUTH=dev uv run pitch-ledger api
# terminal 2
cd studio && npm install && npm run dev      # http://localhost:3000
```

Checks: `npx next typegen && npx tsc --noEmit`, `npm run lint`, `npm run build`.

Producer sign-in with Microsoft Entra ID (MSAL) is added when the API app registration exists (docs/azure-setup.md); until then the console works against an API in `dev` auth mode only.
