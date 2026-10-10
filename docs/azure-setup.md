# Azure setup — provisioning log and lessons

How the Azure side of Pitch Ledger was set up, how to check it, and what went wrong on the way. Keep this current whenever a resource, role or setting changes.

No secrets, tenant IDs or client IDs go in this file. Use placeholders like `<tenant-id>`.

## Current state (2026-10-10)

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
| Event Hubs namespace | `pitch-ledger-feed` (Basic) | Live feed; created 2026-10-09; recurring cost, delete after judging |
| Event hub | `match-events` | One partition key per match |
| Roles on the namespace | **Azure Event Hubs Data Sender** and **Data Receiver** → `pitch-ledger-dev` | Verified by a live `stream` + `listen` run (2026-10-09) |
| Ledger | Cosmos DB for NoSQL `pitch-ledger-cosmos` (free tier, throughput capped), database `pitchledger`, container `ledger` (`/match_id`) | Built-in Data Contributor (data plane) → `pitch-ledger-dev`; `check-ledger` passes (2026-10-09) |
| Trigger queue | Service Bus `pitch-ledger-bus` (Basic), queue `triggers` (max delivery 3, lock 5 min) | Data Sender and Data Receiver → `pitch-ledger-dev`; `check-queue` passes (2026-10-09) |
| Live push | Web PubSub `pitch-ledger-push` (Free), hub `studio` | **Web PubSub Service Owner** → `pitch-ledger-dev`; `check-push` and a client round trip pass (2026-10-10) |
| Event store | Azure Data Explorer free cluster (Australia East), database `matchdata` | Located by `ADX_CLUSTER_URI` and `ADX_DATABASE`. `pitch-ledger-dev` is **Ingestor** and **Viewer**; tables created from `infra/adx/tables.kql`; `check-store` passes (2026-10-09) |

Not provisioned yet: Container Apps, Key Vault, Application Insights. They come in Phase 4 with Bicep under `infra/`.

Not available: **Microsoft Fabric**. The free trial isn't offered to this account (2026-10-09). The live feed uses Azure Event Hubs, the event store is the Azure Data Explorer free cluster with an ADX dashboard, and ops alerts live in the producer console. Fabric is a documented future upgrade (docs/architecture.md).

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

## Live feed: Azure Event Hubs (provisioned 2026-10-09)

**Cost first:** Event Hubs has no free tier. The Basic tier with 1 throughput unit costs about **$11/month**, plus about $0.03 per million events (from memory; check the Azure pricing page for East US before creating it). It doesn't scale to zero, so create it when you need the live feed and delete it after judging (Nov 20). One full match is about 130,000 messages, a fraction of a cent in event charges.

Why Basic is enough: one sender (Match Lab), one reader (engine), one consumer group (`$Default`) and 1-day retention. Pick **Standard** (about twice the price) only if something else must read the same hub with its own consumer group, for example Fabric Eventstream later.

### 1. Create the namespace and the hub

1. Azure portal → **Create a resource → Event Hubs → Create**.
2. Resource group: the Pitch Ledger one. Namespace name: `pitch-ledger-feed` (must be globally unique). Region: **East US** (same as Foundry). Pricing tier: **Basic**. Throughput units: **1**.
3. Review + create.
4. In the namespace: **+ Event Hub**. Name: `match-events`. Partition count: **2** (one match uses one partition; more partitions don't help here). Retention: 1 day (Basic).

### 2. Roles for the app (Entra ID, no keys)

On the **namespace** (or just the `match-events` hub) → Access control (IAM) → Add role assignment, members = `pitch-ledger-dev`:

- **Azure Event Hubs Data Sender**: lets it publish (Match Lab, `stream`).
- **Azure Event Hubs Data Receiver**: lets it read (engine, `listen`).

These are data-plane roles, like Foundry User for the models. A management role such as Owner or Contributor on the namespace does **not** let the app send or receive (same trap as lesson 4). Allow 5–10 minutes.

### 3. Settings

```
PITCH_LEDGER_FEED=eventhubs
PITCH_LEDGER_EVENTHUB_NAMESPACE=pitch-ledger-feed.servicebus.windows.net
PITCH_LEDGER_EVENTHUB_NAME=match-events
```

Sign-in reuses `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`, or a managed identity in Container Apps. A SAS connection string (`PITCH_LEDGER_EVENTHUB_CONNECTION`, from the namespace's Shared access policies) also works. It's a secret, so keep it in the environment settings only.

**Claude Code cloud sessions:** the environment's network access must allow **`*.servicebus.windows.net`**. Add it under Allowed domains if the network level is limited. Behind the session proxy the adapter uses AMQP over WebSockets on port 443 automatically; elsewhere it uses plain AMQP (port 5671). `PITCH_LEDGER_EVENTHUB_TRANSPORT=websockets` forces 443 if a firewall blocks 5671.

### 4. Verify

In one terminal (or a background job), start the reader first. It only sees messages sent after it connects:

```
uv run pitch-ledger listen --idle 20
```

Then publish two match minutes at 10x pace (12 seconds, about 2,800 messages):

```
uv run pitch-ledger stream data/M0002 --start-min 60 --minutes 2 --speed 10
```

`listen` prints rows received and send-to-receive latency (p50/p95/p99), and writes `data/live/listen.json`.

### Measured (2026-10-09, from a Claude Code cloud session)

| Run | Messages | Lost | p50 | p95 | Max |
| --- | --- | --- | --- | --- | --- |
| 1 match minute at 1x (23 msg/s) | 1,341 | 0 | 3.3 s | 7.3 s | 9.3 s |
| 30 s at 1x | 680 | 0 | 2.6 s | 6.3 s | 9.3 s |
| 2 match minutes at 10x | 2,695 | 0 | 2.5 s | 4.6 s | 8.2 s |

- A send takes about 0.27 s (round trip through the session proxy). Messages then arrive in **bursts** every few seconds rather than one by one; the best single delivery was 0.6 s. Pinning the reader to each partition made the bursts bigger, so it is the WebSocket path through the proxy, not the SDK's load balancer.
- At 10x the publisher could only send about 3.7 batches a second (one round trip each), so it fell 25 s behind. Real-time pace (1 batch a second) keeps up.
- These are **pessimistic** numbers for this sandbox. Re-measure from Container Apps (same region, plain AMQP), or from your laptop with the same two commands.

### Switching to Fabric Eventstream later

No code changes either way (see docs/architecture.md, Live feed):

- **Keep the hub, add Fabric on top:** in an Eventstream, add this Event Hub as a source and route it to an Eventhouse. Give Eventstream its own consumer group, which needs Standard.
- **Replace the hub:** create an Eventstream custom endpoint source and set `PITCH_LEDGER_EVENTHUB_CONNECTION` to its connection string (Event Hub protocol, SAS). Remove the namespace settings.

## Event store: Azure Data Explorer free cluster

The free cluster costs nothing and has no SLA. It runs the same KQL engine as a Fabric Eventhouse, so tables and queries can move to Fabric later unchanged.

### 1. Cluster and database

1. Go to **dataexplorer.azure.com/freecluster** and sign in. Create the free cluster and a database (done 2026-10-09).
2. Copy the **cluster URI** (`https://<cluster>.<region>.kusto.windows.net`) and the database name.

### 2. Let the app read and write (run in the free cluster's query editor)

```
.add database ['<database>'] ingestors ('aadapp=<application-client-id>;<tenant-id>') 'pitch-ledger-dev'
.add database ['<database>'] viewers ('aadapp=<application-client-id>;<tenant-id>') 'pitch-ledger-dev'
```

- *Ingestors* can write rows (engine, orchestrator); *viewers* can query (dashboard tiles run as you, the signed-in user).
- Use the app's Application (client) ID and the Directory (tenant) ID from Entra ID → App registrations → `pitch-ledger-dev`. If the free cluster refuses an app principal, note the error; the fallback is ingesting as your own user from your laptop.

### 3. Settings

```
ADX_CLUSTER_URI=https://<cluster>.<region>.kusto.windows.net
ADX_DATABASE=<database>
```

**Claude Code cloud sessions:** allow **`*.kusto.windows.net`** in the environment's network access (it covers the query and the `ingest-` hosts).

### Verified (2026-10-09)

From a cloud session, as `pitch-ledger-dev`: Entra ID token for the cluster, `print ok=1`, `.show principal roles` (Ingestor and Viewer on `matchdata`), `.show tables` (none yet); the query and `ingest-` hosts are both reachable.

**Region:** the free cluster is in Australia East while Event Hubs and Foundry are in East US. That's fine for this project: dashboard queries run from your browser, and writes from East US cross regions with a small delay and negligible transfer cost (about 20 MB per match). Keep it unless a new free cluster can easily be created closer.

**Tables are created by you, not the app.** The app stays least-privileged (Ingestor + Viewer can write rows and query but not create tables). The table script will be in the repo for you to run once in the query editor.

### 4. Tables, dashboard, verification

1. **Create the tables (you, once):** open the free cluster's query editor on `matchdata`, paste the whole of `infra/adx/tables.kql` (or the output of `uv run pitch-ledger store-schema`) and run it. It creates every table, its JSON mapping, streaming ingestion and a 10-second batching fallback. Re-running is safe. The result lists each command; a failed line (for example if the free cluster refuses a policy) shows its error.
2. **Check:** `uv run pitch-ledger check-store` (verified 2026-10-09: all PASS, test write readable after 3.5 s, so streaming ingestion works on the free cluster) reports settings, sign-in, roles, tables, and one test write read back.
3. **Load a match:** `uv run pitch-ledger store data/M0002 --store adx` (events, tracking, engine snapshots, triggers, insight status). Live: `uv run pitch-ledger listen --store adx` also writes rows and feed health.
4. **Dashboard:** see the next section.

### Dashboard (control room), step by step

Menu names drift a little between versions of the web UI; when it differs, look for the same idea nearby.

1. **Open** <https://dataexplorer.azure.com> and sign in with the account that owns the free cluster.
2. **New dashboard:** left menu **Dashboards** → **+ New dashboard** → name it `Pitch Ledger control room` → Create.
3. **Data source:** top bar **Data sources** (under the ⋯ or Manage menu) → **+ New data source** → Cluster URI: the value of `ADX_CLUSTER_URI` → **Connect** → Database: `matchdata` → Data source name: `matchdata` → **Create** / Apply → close the pane.
4. **Match picker (parameter):** top bar **Parameters** → **+ New parameter**:
   - Label `Match`, Parameter type **Single selection**, Variable name **`_match_id`** (exactly), Data type **string**.
   - Source **Query** → Data source `matchdata` → query `Events | distinct match_id | order by match_id asc` → Run → Value column `match_id`.
   - Default value **M0002** → **Done** → close. (A default *Time range* parameter may exist; our tiles don't use it, so it can stay or be removed.)
5. **Add each tile** from `infra/adx/dashboard.kql` (8 tiles): **+ Add** → **Add tile** → data source `matchdata` → paste the query (the lines under one `// Tile` heading) → **Run** → **+ Add visual** → set the visual below → name the tile → **Apply changes**.

   | Tile | Visual | Settings |
   | --- | --- | --- |
   | 1. Score and clock | Table | — |
   | 2. Control score over time | **Line chart** | X: `minute`, Y: `control`, Series: `team_id` |
   | 3. Momentum shifts and key moments | Table | — |
   | 4. Latest window per team | Table | — |
   | 5. Insight pipeline | **Pie chart** | Category: `status`, Numeric: `insights` |
   | 6. Insights | Table | — |
   | 7. Feed health | **Time chart** | X: `window_start`, Y: `p50_ms`, `p95_ms` (pick match M0099 to see data) |
   | 8. Ops alerts | Table | Empty until ops alerts are built |

6. **Arrange:** drag tile edges to resize; put tiles 1, 2 and 3 on top.
7. **Auto refresh** (for live matches): ⋯ / Manage → **Auto refresh** → on, every 30 s (or the shortest offered).
8. **Save** (top bar). Switch matches with the **Match** picker; M0002 is the planted late collapse.


## Ledger: Azure Cosmos DB (free tier)

The insight ledger's system of record. **Cost:** the free tier covers 1,000 RU/s and 25 GB; one free-tier account per subscription, chosen when the account is created. With "Limit total account throughput" ticked, the account cannot exceed the free amount, so there is no surprise bill.

### 1. Create the account

1. Azure portal → **Create a resource** → search **Azure Cosmos DB** → Create → **Azure Cosmos DB for NoSQL** → Create.
2. Resource group: the Pitch Ledger one. Account name: `pitch-ledger-cosmos` (globally unique; add a suffix if taken). Location: **East US**.
3. Capacity mode: **Provisioned throughput**. **Apply Free Tier Discount: Apply**. **Limit total account throughput: ticked**.
4. Leave the other tabs at their defaults → Review + create → Create (takes a few minutes).

### 2. Database and container

In the account → **Data Explorer** → **New Container**:

- Database id: **Create new** → `pitchledger`, tick **Share throughput across containers**, Database throughput **Manual**, **400** RU/s.
- Container id: `ledger`. Partition key: `/match_id`.
- OK.

### 3. Let the app read and write (data-plane role)

Cosmos DB's data roles are not in the portal's Access control (IAM) blade; assign it with the Azure CLI in **Cloud Shell**:

1. Portal top bar → **Cloud Shell** icon (`>_`) → **Bash**. If asked about storage, choose **No storage account required** (no cost).
2. Run, with your resource group name:

```
az cosmosdb sql role assignment create \
  --account-name pitch-ledger-cosmos \
  --resource-group <resource-group> \
  --scope "/" \
  --principal-id 93bdbd00-4663-4267-b536-a94dc7b55680 \
  --role-definition-id 00000000-0000-0000-0000-000000000002
```

- `--principal-id` is the **object ID of the `pitch-ledger-dev` service principal** (Entra ID → Enterprise applications → `pitch-ledger-dev` → Object ID). It is not the client ID, and not the object ID shown under App registrations.
- `00000000-0000-0000-0000-000000000002` is the built-in **Cosmos DB Built-in Data Contributor** role (read and write items).

### 4. Settings and check

```
PITCH_LEDGER_LEDGER=cosmos
COSMOS_ENDPOINT=https://pitch-ledger-cosmos.documents.azure.com:443/
```

The endpoint is on the account's Overview page (URI). Cloud sessions: allow **`*.documents.azure.com`** in the environment's network access. Then `uv run pitch-ledger check-ledger` writes, reads and deletes one test document.

## Trigger queue: Azure Service Bus (Basic)

Carries triggers from the detector to the orchestrator, with retries and dead-lettering. **Cost:** Basic has no monthly base fee; it bills per operation (about $0.05 per million, from memory; check the pricing page). A replayed match is a few hundred operations.

### 1. Create the namespace and queue

1. Azure portal → **Create a resource** → **Service Bus** → Create.
2. Resource group: the Pitch Ledger one. Namespace name: `pitch-ledger-bus` (globally unique). Location: **East US**. Pricing tier: **Basic**. Review + create → Create.
3. In the namespace → **+ Queue**: Name `triggers`, **Max delivery count `3`**, **Lock duration 5 minutes**, leave the rest → Create.

### 2. Roles for the app

On the **namespace** → Access control (IAM) → Add role assignment → member `pitch-ledger-dev`:

- **Azure Service Bus Data Sender**
- **Azure Service Bus Data Receiver**

As with Event Hubs, Owner or Contributor alone does not allow sending or receiving. Allow 5–10 minutes.

### 3. Settings and check

```
PITCH_LEDGER_QUEUE=servicebus
PITCH_LEDGER_SERVICEBUS_NAMESPACE=pitch-ledger-bus.servicebus.windows.net
```

The queue name defaults to `triggers` (`PITCH_LEDGER_SERVICEBUS_QUEUE`). `*.servicebus.windows.net` is already allowed for Event Hubs. Then `uv run pitch-ledger check-queue` sends, receives and completes one probe trigger (match `M9999`, reserved for checks).

### Verified (2026-10-09)

`check-ledger` and `check-queue` pass. A full replay of M0002 through both (offline models, 63 s end to end) matched the local run exactly, and a separate process read the whole ledger back from Cosmos DB, including the momentum insight's audit trail (draft, planted error, Auditor block, revision, pass).

### Use them

`uv run pitch-ledger replay data/M0002 --approve --inject-error --ledger cosmos --queue servicebus` runs the agents with the ledger in Cosmos DB and the triggers through Service Bus (the flags override the settings).

## Live push: Azure Web PubSub (Free tier)

Sends small "something changed" signals to the Studio so the fan view and the producer console update at once instead of polling. **Cost:** the Free tier (Free_F1) has no charge: 20 concurrent connections and 20,000 messages a day, plenty for the demo. It's limited to one Free unit per subscription.

### 1. Create the resource

1. Azure portal → **Create a resource** → search **Web PubSub Service** → Create.
2. Resource group: the Pitch Ledger one. Name: `pitch-ledger-push` (globally unique). Region: **East US**. Pricing tier: **Free**. Review + create → Create.
3. No hub needs creating: the hub `studio` exists as soon as it's used.

### 2. Role for the app

On the resource → Access control (IAM) → Add role assignment → **Web PubSub Service Owner** → member `pitch-ledger-dev`. This lets the server send to groups and sign client URLs. Allow 5–10 minutes.

### 3. Settings and check

```
PITCH_LEDGER_PUSH=webpubsub
PITCH_LEDGER_WEBPUBSUB_ENDPOINT=https://pitch-ledger-push.webpubsub.azure.com
```

The endpoint is on the resource's Overview page (Host name, with `https://`). Cloud sessions: allow **`*.webpubsub.azure.com`** in the environment's network access. Then `uv run pitch-ledger check-push` signs a client URL and sends one test signal.

Browsers connect straight to Web PubSub with a short-lived URL from `GET /api/v1/realtime/negotiate?match_id=…` (fans; `/negotiate/producer` needs producer sign-in). Each URL joins one group: `fans.<match>` or `producers.<match>`.

### Verified (2026-10-10, from a Claude Code cloud session)

- `check-push`: client URL signed by the service; test signal accepted by hub `studio`.
- Client round trip (a WebSocket client on the signed URL, the app's notifier sending): `fans.M0012` got `overlays_changed` and `producers.M0012` got `insight_changed` about 55 ms after the send. A decoy sent to the other audience's group first never arrived, so groups isolate fans from producer signals.
- Through the API (`PITCH_LEDGER_API_AUTH=dev`, file ledger): fan and producer sockets from `/realtime/negotiate`, then `POST /insights/<id>/decision` approve: the producer socket got `insight_changed` 0.21 s and the fan socket `overlays_changed` 0.27 s after the call.
- Studio in Chromium: the fan view showed **live**, connected to `wss://pitch-ledger-push.webpubsub.azure.com/client/hubs/studio`, and showed the newly aired overlay 0.6 s after approval (it polls only every 30 s while live).

## API sign-in for producers (needed with the Studio app)

Not set up yet; do this when the Studio producer console is built. Until then run the API with `PITCH_LEDGER_API_AUTH=dev` on your own machine only.

1. Entra ID → **App registrations** → **New registration** → name `pitch-ledger-api`, single tenant → Register. Note its **Application (client) ID**.
2. **Expose an API** → Application ID URI → Add (accept `api://<client-id>`) → **Add a scope** `access_as_producer` (admins and users can consent).
3. **App roles** → **Create app role**: display name `Producer`, allowed member types **Users/Groups**, value **`Producer`**.
4. **Manifest** → set `"requestedAccessTokenVersion": 2` (under `api`) → Save. The API checks v2 tokens: issuer `https://login.microsoftonline.com/<tenant>/v2.0`, audience = the client ID.
5. Entra ID → **Enterprise applications** → `pitch-ledger-api` → **Users and groups** → **Add user/group** → yourself, role **Producer**.
6. Settings: `PITCH_LEDGER_API_AUTH=entra`, `PITCH_LEDGER_API_AUDIENCE=<client ID>` (the tenant comes from `AZURE_TENANT_ID`).

The Studio app gets its own app registration (a single-page app) that requests the `access_as_producer` scope; that comes with the Studio.

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
| 8 | `listen` against Event Hubs: `ImportError('Please install websocket-client library to use sync websocket transport.')`, retried every ~30 s | The SDK needs an extra package for AMQP over WebSockets; tests with fake clients didn't catch it | Added `websocket-client` as a dependency, plus a test that checks it is installed |
| 9 | Event Hubs latency of several seconds, arriving in bursts | The WebSocket path through the cloud session's proxy releases received data in bursts; each send is a ~0.27 s round trip | Treat sandbox numbers as an upper bound; measure from Container Apps or a laptop (plain AMQP) |
| 10 | `listen` took minutes to exit after the last message | The SDK's `close()` blocked on the stalled link | `close()` now gives up after 10 s (daemon thread) |
| 11 | New settings (feed, ADX) were saved but missing in the next session | The values didn't reach this session's environment (same pattern as lesson 1) | After editing, start a new session and check the variable *names* are present before testing; make sure you edit the environment this session uses |
| 12 | `stream` to Event Hubs fails with *"The request was terminated because the entity is being throttled"* (50002) | Basic with 1 throughput unit accepts about 1,000 messages a second; 60x replay is about 1,380 | Replay at 40x or less (25x is comfortable), or add throughput units (cost) |
| 13 | `studio/src/lib/` missing in CI ("Cannot find module '@/lib/api'") | The Python `.gitignore` rule `lib/` also matched the Studio's source folder, so it was never committed | An exception `!studio/src/lib/`; check a fresh clone when adding folders with common names |
| 14 | A client received each push signal as a quoted string, so the Studio ignored it; `check-push` still passed | The adapter sent `json.dumps(...)` with `content_type="application/json"`, and the SDK serializes JSON content again (double encoding). The fake client in the tests decoded once and hid it | Send the dict, not a string; the fake now serializes like the SDK and fails on a string. Verify push with a real client round trip, not only a send |

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

- [x] Cosmos DB and Service Bus created; `check-ledger` (write, read, delete in 1.2 s) and `check-queue` (send, receive, complete in 4.4 s) pass; full M0002 replay with `--ledger cosmos --queue servicebus` (offline models): 56 triggers through the queue, no dead letters, 18 insights aired, 270 overlays, planted error caught; read back from Cosmos in a new process (56 triggers, 18 insights, 70 audits, 270 overlays) (2026-10-09).

- [ ] Remove the Azure AI Administrator role from `pitch-ledger-dev` (least privilege).
- [ ] Put a calendar reminder a week before the client secret expires.
- [ ] Confirm a budget alert exists on the subscription.
- [ ] Phase 4: deployed services use a **managed identity** (no client secret) with Foundry User. Turn off key auth on the Foundry resource. Put all resources in Bicep under `infra/`.
- [ ] Optional: deploy a stronger model for the `STRONG` tier (Explainer, Auditor, Narrator) before the demo recording.
- [x] Event Hubs: namespace `pitch-ledger-feed`, hub `match-events`, Data Sender and Data Receiver roles; verified with `listen` + `stream` (2026-10-09).
- [ ] Event Hubs: delete the namespace after judging (Nov 20).
- [x] Web PubSub: `pitch-ledger-push` (Free), Web PubSub Service Owner role, settings; `check-push`, client round trip and Studio live update verified (2026-10-10).
- [x] Feed settings and `ADX_CLUSTER_URI` / `ADX_DATABASE` in the cloud environment; `listen` + `stream` pass with them (1,341/1,341 messages, p50 2.3 s from the cloud session).
- [x] ADX: `pitch-ledger-dev` is Ingestor and Viewer on `matchdata`; `*.kusto.windows.net` reachable.
- [x] ADX tables created; `check-store` passes (streaming write readable after 3.5 s); M0002 loaded (`store --store adx`, 129,781 rows in 23 s); live `listen --store adx` verified with test match M0099 (1,343 rows, feed health).
- [ ] ADX: build the dashboard from `infra/adx/dashboard.kql`.
- [ ] ADX (optional): clear the test matches before the demo, as database admin: `.delete table Events records <| Events | where match_id in ('M0099', 'M0012')`, and the same for Tracking, TeamState, Triggers, InsightStatus and FeedHealth. The event store only appends, so a rerun of a live match adds a second copy.
- [ ] Service Bus: 16 triggers of test match M0012 sit in the `triggers` dead-letter queue from a run before the send/receive race fix (reason `stale`); harmless (later runs ignore them), clear them in the portal (Service Bus Explorer, dead-letter, receive) if you like.
- [x] Live service verified on Azure (2026-10-09): M0012 streamed at 25x through Event Hubs; 56 triggers via Service Bus, 18 insights aired, momentum insight corrected, ledger in Cosmos DB (56 triggers, 18 insights, 70 audits, 270 overlays), rows in ADX; trigger to outcome p50 3.9 s, max 17 s (offline models).
- [x] Fabric-only pieces replaced (2026-10-09): ADX free cluster (event store), ADX dashboard (control room), producer-console ops alerts. Fabric kept as a future upgrade.
