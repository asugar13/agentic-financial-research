# Repository workflow

- Agents may open and update PRs but must never merge them. The user is the navigator and handles merging.
- Ask explicitly before every commit, including local checkpoints. Agreeing to a plan is not approval to commit. Leave changes uncommitted and unstaged by default.
- The user is the sole author: no `Co-Authored-By` trailers and no "Generated with" footers in commits or PRs.
- PR descriptions are short plain prose in the user's voice: what changed and why. No testing or verification section.
- Never commit `docs/pass-1.md` or `docs/exa-demo.md`; they are personal notes.
- Help level (see the AWS rules below): HIGH for AWS and infrastructure changes; brief high-level summaries are enough for demo feature code.
- To stop a local server, kill only the process listening on its port (`lsof -ti tcp:PORT -sTCP:LISTEN`), never every process connected to it (that includes the browser).

<!-- BEGIN AWS Agent Toolkit rules -->

## AWS configuration for this repository

- AWS project: `FinancialResearch`.
- AWS CLI profile: `financial-research`.
- Selected Region: `eu-north-1` (Stockholm).
- Use `--profile financial-research` for AWS CLI commands and specify `eu-north-1` for Regional AWS operations.
- The toolkit catalog and configured MCP endpoint use `us-east-1`; that does not change the project's selected Region.

The AWS rules below are copied from [AWS's official new-experience rules](https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/rules/aws-starter-rules.md), following the [Agent Toolkit setup guide](https://raw.githubusercontent.com/aws/agent-toolkit-for-aws/refs/heads/main/setup-instructions/setup.md). The repository configuration above is specific to this project.

# AWS Guidance for the new AWS experience

This user has signed up for the new AWS experience. This experience lets you sign into AWS using a social provider and requires the following additional context.

Where this guidance conflicts with the project's own instructions, the project's instructions take precedence.

## Context

### Terminology:

- Say "project" instead of "account" — a project contains an AWS account and settings for sharing with other collaborators
- Say "team member" instead of "IAM user" — users are invited by email, not created or federated in IAM
- Say "AWS Settings" when referring to management tasks at [settings.aws.com](https://settings.aws.com/) (project management, billing, team members, spend limits). Users view their actual AWS resources in the AWS Management Console.
- Say "selected Region" when referring to the user's Region — not "home Region"
- The user has a managed IAM experience. This includes a managed service control policies (SCP) and resource control policies (RCP) that govern the use of AWS. They will still need to use IAM to create policies to let services work with each other. If there are questions about the SCPs or RCPs, go to the documentation at https://docs.aws.amazon.com/accounts/latest/reference/scps-and-rcps-for-projects.html

### Constraints:

- All projects share a single AWS Region determined by the user's contact address. Resources cannot be created in other Regions
- When developing:
  - MUST create all Regional resources in the project's assigned Region
  - You CAN create AWS WAF and Cloudwatch Logs resources in us-east-1 when there are global resources (like a global WAF instance) that require a connection to dependencies in us-east-1. You should not use these for any other reason, because resources in the selected Region will provide lower cost (due to no cross-Region traffic), increased availability (due to no cross-Region traffic), and easier manageability (due to not needing to look in another Region). When you need to do an inventory of resources, you need to look in both the selected Region and us-east-1 for Cloudwatch Logs or WAF resources.
  - MUST NOT attempt to create Lambda, API Gateway, or other Regional resources in any other Region
  - MUST direct users to confirm their Region in AWS Settings > View all projects > Overview > Additional Info > Region. If the user cannot confirm their Region, check in ~/.aws/config
  - MUST NOT use Lambda@Edge — excluded from both Lambda and CloudFront
  - MUST NOT use CloudFormation StackSets — no multi-account or multi-Region deployments
  - MUST NOT attempt cross-Region actions — no cross-Region replication for DynamoDB/S3/RDS, no multi-Region KMS keys
  - MUST NOT use Route 53 cross-Region routing — geolocation, latency-based, and failover routing policies are not available
  - CloudFront is a global service and its actions ARE allowed in `us-east-1`. A user can create a CloudFront distribution pointing to their project-region Lambda function URL or API Gateway. However, Lambda and API Gateway themselves MUST NOT be created in `us-east-1` — they must be in the project Region.
  - Reduced availability in `eu-north-1` specifically: Amazon Rekognition, Amazon Textract, Amazon Personalize, AWS App Runner are not available in that Region.
- IAM permissions for human access are managed by AWS. Don't assign roles to team members unless absolutely necessary
- The user may have a spend limit if they are on the paid plan. The limit that pauses their project if it's exceeded. If resources suddenly become inaccessible, ask if they have a spend limit configured. Only project owners can modify a spend limit.
- When developing:
  - MUST ask about spend limit status if the user reports sudden "Access Denied" errors on operations that previously worked
  - MUST direct users to check spend status in AWS Settings > Billing
  - MUST check if a user has upgraded their account to the paid plan
  - MUST ask the user if they want to clean up the successfully created resources or keep them to reduce cost
- The user sets up billing, creates spend limits, and retrieves and pays invoices in AWS Settings. The user creates budgets and optimizes their costs in the AWS Billing and Cost Management console
- Not all AWS services are available. If a service isn't working, do the following:
  1. Run the command `aws freetier get-account-plan-state`
  2. If accountPlanType": "FREE", check the [Free Tier supported services list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#supported-services-free-tier) next,
  3. If accountPlanType": "PAID", check the [Paid Tier supported services list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#supported-services-paid-plan).
  4. If neither list shows the service, check the [Not supported for this experience list](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html#unsupported-services). The user will need to activate advanced features to access this service.
- Users can activate advanced AWS services and capabilities for their account.
- For any AWS task or question — building, configuring, migrating, debugging, or operating a service, writing SDK code, authoring CDK/CloudFormation, or answering how a service works — first call `aws___search_documentation` with topic `agent_skills` and load the matching skill with `aws___retrieve_skill`. Do this even when you believe you know the answer: skills carry current service behavior that your training may not. Prefer the skill's guidance over general knowledge. Skip it only for work that does not involve AWS.

### Help level

- help_level (required): LOW, MEDIUM, or HIGH. While a user is building, you MUST ask the user: "How much guidance would you like from me? Low (I only flag security risks), medium (I ask a couple of clarifying questions if something seems off), or high (I explain what I'm doing, suggest alternatives, and flag best practices)."

You CAN update this rule file to save a user's help_level.

Constraints for each level:

**LOW:**

- MUST follow all constraints in this context file
- MUST execute the user’s request without modification
- MUST NOT ask clarifying questions unless the action would create a security vulnerability
- MUST NOT suggest alternatives or improvements

**MEDIUM:**

- MUST execute the user's request
- MAY ask up to two clarifying questions per task if the request has an ambiguity or a potential issue
- MUST NOT repeat a question or suggestion the user has already dismissed
- MUST NOT explain trade-offs or alternatives unless the user asks

**HIGH:**

- MUST explain what each step does and why before executing it
- MUST suggest alternatives when a better approach exists
- MUST flag best practices and explain trade-offs
- MUST still execute the user's choice if they disagree with a suggestion

<!-- END AWS Agent Toolkit rules -->

# Project context

An agentic financial research app: a FastAPI backend and a React frontend. A LangGraph research agent chooses tools (Yahoo prices, FRED macro data, Exa web news, quantitative analysis) and writes cited briefs, using Qwen3 32B on Bedrock when hosted, Ollama locally, or an offline demo model.

The current direction is an **Exa-powered event radar for retail brokers**: a portfolio view whose price moves are explained by cited company events, so a broker's users never leave the app to find out why their stocks moved. Demo prep notes live in `docs/exa-demo.md` (local and git-ignored).

## Layout

- `src/financial_research/radar.py`: watchlist scans (one Exa structured search per company), price moves against SPY, price history, and the file cache.
- `src/financial_research/web.py`: FastAPI app. `/api/config`, `/api/prices`, `/api/scan`, `/api/runs` (agent runs); serves `frontend/dist`.
- Research agent: `workflow.py`, `tools.py`, `research_model.py`, `bedrock_model.py`, `ollama_model.py`, `news.py`, `prices.py`, `macro.py`, `quant.py`, `runner.py`.
- `frontend/src`: `main.tsx` (tabs, shared watchlist and scan, error boundaries), `Portfolio.tsx` and `PriceChart.tsx`, `Radar.tsx`, `ResearchForm.tsx` and `ResearchRun.tsx` (custom research and agent runs), `watchlist.ts` (shared helpers), `api.ts` (types).
- `deployment/`: `deploy.py`, `host.sh`, `stack.yaml`, `Caddyfile`. See `deployment/README.md`.

## Running and checking locally

- Install with `.venv/bin/python -m pip install -c requirements.lock -e '.[dev,web,news,bedrock]'`, then `npm ci --prefix frontend` and `npm run build --prefix frontend` (the build also type-checks).
- Start with `.venv/bin/python -m uvicorn financial_research.web:app --port 18001`. Port 8000 is often taken on the user's Mac.
- `EXA_API_KEY` comes from the environment or the local `.env`. Bedrock needs `aws login --profile financial-research` and `AWS_PROFILE=financial-research`.
- Tests: `.venv/bin/python -m pytest`. Exa and Yahoo are always faked in tests.
- After a frontend rebuild, hard-refresh open tabs: an old bundle crashes when an API field is renamed.

## How the event radar works, and why

- One Exa `/search` per company: `type: "auto"`, `category: "news"`, a published-date window, 10 results, and `outputSchema` = `EVENT_SCHEMA` (`reported_date`, `scheduled_date`, `category`, `headline`, `why_it_matters`). Exa allows at most 10 properties and 2 nesting levels; a test guards this.
- `auto`, not `deep`: in testing, `deep` merged several events into one row, and it costs $0.012 per search instead of $0.007.
- `category: "news"` stays: without it, law-firm solicitations and aggregator pages appeared, and dates got worse.
- Each event's sources and confidence come from Exa's per-field `grounding`; an event's confidence is its weakest field's.
- The search uses the company name from Yahoo's metadata (`longName`) to avoid ambiguous tickers.
- Price moves start from `reported_date`, when the news became public. A future `scheduled_date` marks the event as upcoming. Exa occasionally takes a later article's date.
- A price move runs from the last close before the news to the first close after it, minus SPY over the same days. Events close together share a window; it shows a reaction, not a cause.
- Cache: JSON per kind, ticker, and lookback under `runs/cache/v{CACHE_VERSION}/`. Events are reused for 12 hours and prices for 30 minutes. If a live call fails, the last good copy is served with a notice. Failed scans are never cached. Bump `CACHE_VERSION` when a cached shape changes.
- Cost: about $0.007 per company per scan.

## Open items and reminders

- **Next: search settings** behind a collapsed panel: include and exclude domains (`includeDomains` / `excludeDomains`), a free-text focus appended to the query, results per company (10 or 25), and client-side filters for event type and minimum confidence.
- **Technical catch: the cache key must include the search settings.** Otherwise a tweaked search returns the cached default results and the settings appear to do nothing. Client-side filters (event type, confidence) need no new key.
- **Revisit later: a `deep` search toggle.** Left out because `deep` merges several events into one row; it would need schema or prompt changes, or splitting rows.
- **Revisit later: a custom column where the model drafts the schema.** Left out because it rewrites the schema. Validate drafts against Exa's limits and keep preset schemas as a fallback.
- **Research this event:** decide between the current Bedrock agent and Exa's Agent API, and between an inline brief and a separate page. It depends on PR #16 (Bedrock tool-selection fix).
- **Monitors:** the production path for intraday updates on top of the daily scan. Its webhook needs an unauthenticated, signature-verified path past Caddy's basic auth.
- **Deployment:** the live server still runs an older release without the radar. The next deploy also replaces the security group, because its description changed in #19; a change-set preview showed the server is modified in place and keeps its IP. PR #23 (sample portfolio) is awaiting a decision.
