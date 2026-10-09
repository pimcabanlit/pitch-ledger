# Architecture

Fabric carries the live match data, Container Apps runs the Python engine and the agents, and Microsoft Foundry supplies models and guardrails. Every insight is audited before it reaches a screen.

```mermaid
flowchart TB
    feed["Match Lab feed<br/>(Container Apps job)"]

    subgraph fabric["Microsoft Fabric · Real-Time Intelligence"]
        es["Eventstream<br/>ingest and route"]
        eh["Eventhouse<br/>raw events, KQL"]
        lh["Lakehouse<br/>medallion history (stretch)"]
        act["Activator<br/>ops alerts"]
        dash["RTI dashboard<br/>control room"]
        es --> eh
        eh -.-> lh
        eh --> act
        eh --> dash
    end

    subgraph aca["Azure Container Apps"]
        engine["Stats and Momentum Engine<br/>rolling metrics, in code"]
        detector["Moment detector<br/>fires triggers with evidence"]
        mcp["Stats MCP server<br/>read-only stats tools"]
        orch["Match orchestrator<br/>Agent Framework: Explainer ⇄ Auditor,<br/>Narrator, Personalizer, Translator"]
        studio["Studio app<br/>producer console (Entra) + fan view"]
    end

    sb["Service Bus<br/>trigger queue, retries, dead letter"]
    foundry["Microsoft Foundry<br/>models, content safety,<br/>evaluations, tracing"]
    ledger["Insight ledger<br/>Cosmos DB, audit trail"]
    pubsub["Web PubSub<br/>live overlay push"]
    viewers["Producer and fans<br/>any mode, any language"]

    feed --> es
    es --> engine
    engine --> detector
    engine --> mcp
    detector --> sb
    sb --> orch
    mcp <--> orch
    orch --> foundry
    orch --> ledger
    ledger --> pubsub
    pubsub --> studio
    studio --> viewers
    studio -- approvals --> orch
```

Across every service: Microsoft Entra ID (managed identities, producer sign-in), Key Vault, Application Insights / Log Analytics, GitHub Actions, Cost Management budget alerts.

## How one moment flows

1. **Generate.** The Match Lab replays a synthetic match and sends events and 1 Hz tracking samples to the Fabric Eventstream.
2. **Ingest and store.** The Eventstream routes events to the Eventhouse (storage, queries) and to the Stats and Momentum Engine (live processing). Validation rules reject or flag bad rows.
3. **Compute.** The engine updates team and player state in rolling 5-minute windows: possession, turnovers, PPDA, control and chaos scores, speeds and distances.
4. **Detect.** The Moment detector sees, for example, the away side's control score fall 20+ points and hold, then puts a trigger with evidence event IDs on the Service Bus queue.
5. **Explain.** The orchestrator picks up the trigger. The Explainer pulls numbers through the Stats MCP server and drafts an insight (headline, explanation, metrics, evidence).
6. **Audit.** The Auditor recomputes every number through the same tools. A mismatch goes back to the Explainer, at most twice; then approve or block. The decision is written to the ledger.
7. **Approve.** The producer approves in the Studio app (low-risk types such as speed alerts can auto-approve).
8. **Personalize and translate.** Personalizer writes analyst, casual, club, player and single-metric versions concurrently; Translator renders each language concurrently, numbers checked unchanged.
9. **Deliver.** The Overlay builder sets template, timing and priority; overlays are saved to the ledger and pushed through Web PubSub.
10. **Recap.** At half-time and full-time the orchestrator hands off to the Narrator; recaps go through the same audit.

## Components

| Component | Azure service | Role | Challenge stage | Scope |
| --- | --- | --- | --- | --- |
| Match Lab | Container Apps job (Python) | Synthetic matches + planted scenarios; live replay | (a) ingest, (v) data | Core |
| Eventstream | Fabric RTI | Ingest and route events and tracking | (a) | Core |
| Eventhouse | Fabric RTI (KQL DB) | Raw events; time-window queries; eval checks | (a), (b) | Core |
| RTI dashboard | Fabric | Producer and analyst control room | (b), (d) | Core |
| Activator | Fabric | Ops alerts: feed gaps, validation rejects, blocked insights | Ops | Core |
| Lakehouse (Bronze/Silver/Gold) | Fabric OneLake | Match history | (b) | Stretch |
| Stats and Momentum Engine + Moment detector | Container Apps (Python) | Deterministic metrics and triggers | (b), (c) | Core |
| Service Bus queue | Service Bus | Trigger queue; retries, dead letter | (c) | Core |
| Stats MCP server | Container Apps | Read-only stats tools for agents | (b), (c) | Core |
| Match orchestrator | Container Apps + Microsoft Agent Framework | Five agents + Overlay builder | (c), (d), (e) | Core |
| Microsoft Foundry | Foundry | Models, content safety, evaluations, tracing | (c), (e) | Core |
| Insight ledger | Cosmos DB (free tier) | Insights, evidence, audits, approvals, overlays, viewer profiles | (c), (d) | Core |
| Web PubSub | Web PubSub (Free tier) | Live overlay push | (d) | Core |
| Studio app | Container Apps (web) | Producer console + fan view with mode, club, player, metric, language | (d), (e) | Core |
| Fabric data agent | Fabric + Foundry | "Ask the match" for analysts | (e) | Stretch |

## Agents and orchestration

| Agent | Job | Model tier | Pattern |
| --- | --- | --- | --- |
| Explainer | Why a moment matters, citing evidence events | Stronger | Sequential; maker in maker-checker loop |
| Auditor | Recomputes every number; checks claims, tone, neutrality; approve, correct or block | Stronger | Checker (max 2 rounds) |
| Narrator | Player context, milestones, half-time and full-time recaps | Stronger | Handoff at breaks; output audited |
| Personalizer | Analyst, casual, club, player and single-metric versions | Small, fast | Concurrent fan-out per mode |
| Translator | Every version into each language, numbers unchanged | Small, fast | Concurrent fan-out per language |

Failure handling: a model error retries once, then falls back to a plain template overlay built from code-computed numbers; a blocked insight never airs and is logged; Service Bus dead-letters a trigger that keeps failing.

## Enterprise controls (built)

- **No numbers from the model.** Every figure comes from code via read-only MCP tools; the Auditor recomputes before air.
- **Evidence on every insight**, stored in the Cosmos DB ledger with continuous backup.
- **Human in the loop:** Entra ID sign-in; only the producer role approves or blocks.
- **Managed identities, no keys**; Key Vault only for secrets that cannot use Entra ID.
- **Content safety** policies on every model deployment, as code.
- **Agents, prompts and infrastructure as code**, deployed by GitHub Actions; model versions pinned.
- **Data validation** at ingest.
- **Evaluations:** planted scenarios as a test suite (detection rate, false alerts, factual accuracy), tracked in Foundry.
- **Observability:** Application Insights, Log Analytics, Foundry tracing from event to overlay.
- **Cost controls:** token caps per agent, small model for rewrites, budget alerts, scale-to-zero, Fabric trial / paused capacity.

## Production path (documented, not built)

Private endpoints and a virtual network; Application Gateway with WAF and DDoS Protection; Azure Firewall for agent egress; zone-redundant and data-zone model deployments; API Management in front of models; Defender for AI services; Purview over Fabric data; separate dev, test and production Foundry resources.

## Cost plan (no hackathon credits provided)

| Item | Approach |
| --- | --- |
| Azure subscription | Personal Azure free account ($200 credit, 30 days) in the personal Default Directory; upgrade to pay-as-you-go before credit expires so the demo stays live until judging ends (Nov 20) |
| Fabric | 60-day Fabric trial capacity (64 CU) |
| Container Apps | Consumption plan free grant; scale to zero |
| Cosmos DB | Lifetime free tier (1000 RU/s, 25 GB) |
| Web PubSub | Free tier (20 connections, 20k messages/day) |
| Container images | GitHub Container Registry (free for public images) instead of Azure Container Registry |
| Models | Main cost; token caps, small model for rewrites, develop locally first |
| Guardrail | Budget alerts at $25 and $50 |

**Never** use the former employer's tenant, subscription or credits.

## Open items

- [ ] Latency spike (Oct 13–14): time one event from Eventstream to the Python engine. If too slow, engine reads Event Hubs directly; Fabric stays for storage and dashboards.
- [ ] Hosted agents: if time allows, test the orchestrator as a Foundry hosted agent.
- [ ] Confirm agent-capable models available in the chosen region (one stronger, one small).
- [ ] Fabric sign-up may need a work-style user in the personal directory (Entra ID → Users → New user).
