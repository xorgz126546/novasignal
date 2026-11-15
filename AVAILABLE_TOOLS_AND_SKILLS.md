# Available tools and skills (discovered 2026-11-13)

Inventory taken at the start of development in the Claude Code cloud session. "Connected" means the tool
responded in this session; it does not mean it was used (see CONNECTORS_AND_SKILLS_USAGE_REPORT.md).

## Execution environment

| Tool | Capabilities | Installed | Connected / authorized | Contribution to CRYPTONEX AI | Limitations |
|---|---|---|---|---|---|
| Bash / file tools | run code, edit files | yes | yes | all development | ephemeral container |
| Python 3.13 + pip | backend runtime, tests | yes | yes | FastAPI backend, pytest | — |
| Node 22 + npm | frontend toolchain | yes | yes | Vite/React build, vitest | — |
| Chromium + Playwright | headless browser | yes | yes | E2E check of the dashboard + screenshots | — |
| PostgreSQL 16 (local binaries) | database server | yes | yes (started locally for tests) | verified schema & tests on Postgres | local only |
| Docker CLI | container builds | CLI only | **no daemon** | — | Dockerfiles couldn't be build-tested |
| Network: Binance public data | market data | — | `data-api.binance.vision` **allowed** (environment updated with user approval); `api.binance.com` → **HTTP 451** (region-restricted) | live integration test | — |
| Network: api.telegram.org, cryptopanic.com, api.anthropic.com | external APIs | — | not in allow-list | — | tested with mocks only |
| git + GitHub (repo `xorgz126546/novasignal`) | source control | yes | yes | commits + push to `claude/cryptonex-ai-trading-saas-evhe6f` | scoped to this repo |

## MCP servers / connectors

| Name | Capabilities | Installed | Connected / authorized | Possible contribution | Limitations |
|---|---|---|---|---|---|
| GitHub MCP | repos, PRs, issues, Actions | yes | yes | repository operations, CI logs | PR creation only if requested |
| Firebase MCP (plugin) | projects, hosting deploy, rules, SDK config | yes | **not authenticated** (no Firebase user/project) | Firebase Hosting for the frontend | user must `firebase login`; nothing deployed |
| claude-code-remote | environment, sessions, scheduled tasks | yes | yes | network allow-list for Binance (used) | — |
| Claude Docs | living documents | yes | yes | could host docs | docs kept in repo instead |
| Canva | design creation/export | yes | yes | marketing visuals/logo | not needed (logo built as SVG) |
| Graphify | code-graph queries | yes | yes | code navigation | new repo not indexed |
| Remote Desktop Commander | user's device files/processes | yes | yes | — | not appropriate for this task |
| Gmail, Google Calendar, Google Drive | email/calendar/files | installed | **not connected** | — | — |
| meta | — | installed | disconnected | — | — |
| Data plugins: Amplitude (US/EU), Atlassian, Definite, Hex | analytics/BI | yes | **failed** (proxy 403) | future SaaS analytics | network policy |
| Data plugin: BigQuery | warehouse | yes | **needs authentication** | — | OAuth not available here |
| Data plugins: Databricks, Snowflake | warehouse | yes | status unknown (still connecting) | — | not needed |

## Skills (installed)

| Skill | Relevant? | Notes |
|---|---|---|
| `claude-api` | **yes** | read before writing the optional Claude narrative (model id, SDK usage, refusal fallbacks) |
| `dataviz` | **yes** | read before writing charts; palette validator run |
| `artifact-design`, `artifact-capabilities`, `artifact-diagramming` | marginal | for claude.ai artifacts; the product UI lives in the repo |
| `security-review`, `code-review`, `simplify` | yes (QA) | security checklist applied manually (see report) |
| `run` | yes | app launched and driven in a browser directly |
| `anthropic-skills:ui-ux-pro-max`, `ui-styling`, `design` | possible | not loaded; design spec from the prompt was followed directly |
| `data:*` (analyze, statistical-analysis, validate-data…) | possible later | for analysing 30-day results |
| `anthropic-skills:docs`, `docx`, `pdf`, `pptx`, `xlsx` | not needed | docs are Markdown in repo |
| `session-start-hook`, `update-config`, `keybindings-help`, `loop`, `fewer-permission-prompts`, `plugin-authoring`, `init`, `deep-research`, `morning`, `import-memory`, `skill-creator`, `google-workspace`, `built-in-browser`, `chrome-browser`, `computer-use` | no | unrelated to building this product |

## Sub-agents

The Agent tool (general-purpose / Explore / Plan sub-agents) was available. Development was done sequentially
by one session, because the phases depend on shared schemas and parallel edits would have conflicted.
