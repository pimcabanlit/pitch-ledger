# Azure setup — provisioning log and lessons

How the Azure side of Pitch Ledger was set up, how to check it, and what went wrong on the way. Keep this current whenever a resource, role or setting changes.

No secrets, tenant IDs or client IDs go in this file. Use placeholders like `<tenant-id>`.

## Current state (2026-10-09)

| Item | Value | Notes |
| --- | --- | --- |
| Directory (tenant) | Personal Default Directory | Never the former employer's tenant (CLAUDE.md non-negotiable 5) |
| Subscription | Personal subscription | Free credit, then pay-as-you-go in early November (build plan, Phase 5) |
| Foundry resource | `inside-the-game` (kind: Foundry / AI Services), East US | Holds the model deployments |
| Foundry project | `inside-the-game` | Endpoint: `https://inside-the-game.services.ai.azure.com/api/projects/inside-the-game` |
| Model deployment | `gpt-4.1-mini` | Used for **both** tiers for now: `PITCH_LEDGER_MODEL_STRONG` and `PITCH_LEDGER_MODEL_SMALL` |
| App registration / service principal | `pitch-ledger-dev` | Signs in with a client secret from local runs and Claude Code cloud sessions |
| Role on the Foundry resource | **Foundry User** → `pitch-ledger-dev` | Lets it call the models (data plane) |
| Extra role (to remove) | Azure AI Administrator → `pitch-ledger-dev` | Control plane only; not needed for calling models. Remove it for least privilege |

Not provisioned yet: Cosmos DB, Service Bus, Web PubSub, Container Apps, Key Vault, Application Insights, Fabric. They come in Phase 4 with Bicep under `infra/`.

## Provisioning steps (Foundry + service principal)

These are portal steps. They follow the standard path and match what is in place now. Exact menu names drift; when the portal disagrees, trust the portal.

### 1. Subscription guard rails

1. Make sure the portal shows the **personal Default Directory** (top-right account menu → Switch directory).
2. Cost Management → Budgets: create a monthly budget with email alerts (for example at 50%, 80% and 100%).

### 2. Foundry resource, project and deployment

1. Create a **Foundry** resource (search "Foundry" in the portal or use ai.azure.com). Name: `inside-the-game`, region **East US**. Region matters: check that the model you want is offered there.
2. Create a project in it. Name: `inside-the-game`.
3. In the project, open Models + endpoints → Deploy model → `gpt-4.1-mini`, deployment type **Global Standard** or **Standard** (pay per token, no hourly charge). Avoid *Provisioned* types, which bill by the hour. Check which type the current deployment uses under Models + endpoints.
   - The deployment name is what goes in `PITCH_LEDGER_MODEL_*`. It is not always the same as the model name.
4. Copy the **project endpoint** from the project overview page. It has the form `https://<resource>.services.ai.azure.com/api/projects/<project>`.

### 3. App registration (service principal) for code sign-in

1. Microsoft Entra ID → App registrations → New registration. Name: `pitch-ledger-dev`, single tenant, no redirect URI.
2. From its Overview page, note the **Application (client) ID** and the **Directory (tenant) ID**.
3. Certificates & secrets → New client secret.
   - Copy the **Value** (shown once), not the Secret ID.
   - Pick an expiry after **2026-11-20** (the end of judging) and put a reminder in your calendar a week before it.

### 4. Give the app permission to call the models

1. Open the Foundry **resource** `inside-the-game` → Access control (IAM) → Add → Add role assignment.
2. Role: **Foundry User**. Search "foundry", not "azure ai": this role used to be called *Azure AI User*. If it is missing, **Cognitive Services OpenAI User** also works for OpenAI-family models.
3. Members: *User, group, or service principal* → Select members → `pitch-ledger-dev` → Review + assign.
4. Allow 5–10 minutes before testing.

### 5. Settings for the code

Variables, one per line (see `.env.example`):

```
PITCH_LEDGER_MODEL_PROVIDER=foundry
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
PITCH_LEDGER_MODEL_STRONG=<deployment name>
PITCH_LEDGER_MODEL_SMALL=<deployment name>
AZURE_TENANT_ID=<directory (tenant) id>
AZURE_CLIENT_ID=<application (client) id>
AZURE_CLIENT_SECRET=<client secret value>
```

- **Locally:** put the first four in `.env`, which is gitignored. Either sign in with `az login` (no secret needed) or set the three `AZURE_*` variables in your shell.
- **Claude Code cloud sessions:** put all seven in the cloud environment's settings (session title bar → environment → Edit).
  - A running session never picks up changes: start a new session after editing.
  - Do **not** add an Azure credential injection for `*.azure.com` under Network secrets / connectors. See lesson 3.

### 6. Verify

```
uv run pitch-ledger check-models
```

Every line must be `[PASS]`. It makes one tiny call per deployment, at most four calls with retries.

Then run a capped live replay:

```
uv run pitch-ledger replay data/M0002 --approve --inject-error --provider foundry --only momentum_shift --budget 25
```

## Lessons learned (first live run, 2026-10-09)

Each problem below blocked the first live run. Read the error, find the matching row, apply the fix.

| # | Symptom | Cause | Fix |
| --- | --- | --- | --- |
| 1 | `check-models`: `invalid value for PITCH_LEDGER_MODEL_PROVIDER … (one variable per line)` | Two settings sat on one line (`foundry FOUNDRY_PROJECT_ENDPOINT=https://…`). The settings editor can soft-wrap a long line so it *looks* split | Put the cursor after `foundry`, press Enter, and retype the second variable on its own line |
| 2 | `Unable to get authority configuration for https://login.microsoftonline.com/<id>` or `AADSTS900023 … neither a valid DNS name` | Tenant ID mistyped: a hyphen was in the wrong place | A tenant ID is a GUID in **8-4-4-4-12** groups. Copy it again from Entra ID → Overview and retype the whole value |
| 3 | Sign-in passes, but every model call returns **401 "Access denied due to invalid subscription key or wrong API endpoint"**, even after roles are assigned | The token never reached Azure. The cloud session had an Azure credential injection for `*.azure.com` that replaced our `Authorization` header. A test call to `management.azure.com` answered *"The 'Authorization' header is missing."* | Remove the `*.azure.com` Azure credential injection (claude.ai connectors and/or environment Network secrets). 401 + this message = **no token arrived**, not a missing role |
| 4 | Model call returns **403** with an empty body | The token arrived and is valid, but the identity has no data-plane role | Assign **Foundry User** on the Foundry resource (step 4). *Azure AI Administrator* is not enough: it manages the resource but cannot call models |
| 5 | Can't find "Azure AI User" in the role list | Renamed to **Foundry User** | Search "foundry" |
| 6 | The momentum insight came out as templates in a `--budget 60` run | Calls are spent in match order, about 15 per insight (Explainer 1, Auditor 1, Personalizer 5, Translator 8). Earlier goals used the budget before the 68.9' momentum insight | For the demo, use `--only momentum_shift` (about 18 calls), or raise the budget |
| 7 | A replay meant to be offline made Foundry calls | Once `PITCH_LEDGER_MODEL_PROVIDER=foundry` is set, `replay` defaults to Foundry | Always pass `--provider offline` or `--provider foundry` explicitly |

How to read the error codes:

- **Sign-in error (AADSTS…)**: tenant, client ID or secret is wrong. It's a problem with who the app *is*.
- **401 from the model endpoint**: no usable token reached it. Check proxies and credential injection first, then the endpoint URL.
- **403 from the model endpoint**: the app is recognized but not allowed. Check the role, its scope (the Foundry resource) and that it's assigned to the right identity.
- **In IAM, a service principal row shows its Application (client) ID**, the same value as `AZURE_CLIENT_ID`. A token's `oid` claim is a different ID (the object ID) for the same app. Don't compare those two.

## Observed cost and usage

Pay per token on a Standard or Global Standard deployment, with no hourly charge.

| Run | Calls | Tokens in / out | Approx. cost |
| --- | --- | --- | --- |
| `check-models` | 1 | 34 / 6 | ~$0 |
| `--only momentum_shift,key_moment --budget 60` | 60 | 47,151 / 2,315 | ~2 US cents |
| `--only momentum_shift --budget 25` | 18 | 17,678 / 1,325 | < 1 US cent |

Calls rejected with 401 or 403 never reach the model and are not billed.

## Follow-ups

- [ ] Remove the Azure AI Administrator role from `pitch-ledger-dev` (least privilege).
- [ ] Put a calendar reminder a week before the client secret expires.
- [ ] Confirm a budget alert exists on the subscription.
- [ ] Phase 4: deployed services use a **managed identity** (no client secret) with Foundry User. Turn off key auth on the Foundry resource. Put all resources in Bicep under `infra/`.
- [ ] Optional: deploy a stronger model for the `STRONG` tier (Explainer, Auditor, Narrator) before the demo recording.
