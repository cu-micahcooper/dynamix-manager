# TeamDynamix Executive Analytics Workspace Design

Date: 2026-03-17
Status: Approved for planning
Audience: Cedarville University CIO

## Summary

Build a notebook-first executive analytics workspace for Cedarville University's
TeamDynamix environment. The system will use the TeamDynamix API plus a
report-backed survey dataset to create a local cached analytics store, Jupyter
notebooks for interactive analysis, and Tailwind CSS HTML artifacts for
executive reporting.

The system is intended for a single executive user. It will prioritize a
balanced scorecard across service outcomes, operational efficiency, and staff
sustainability. The first version will support both exploratory analysis and a
weekly report for the CIO's boss that always looks back at the previous
calendar month and presents charts first, commentary second.

## Goals

- Provide an executive view of IT organizational health using TeamDynamix data.
- Support drill-down analysis by team and individual staff member.
- Integrate survey outcomes with operational data so customer experience can be
  interpreted alongside workload and service performance.
- Cache data locally so notebooks and reports are fast, repeatable, and not
  dependent on live API calls during normal use.
- Generate polished HTML artifacts as needed, styled with Tailwind CSS.
- Produce a weekly executive report that summarizes the prior month.

## Non-Goals

- Build a multi-user dashboard application.
- Implement role-based access control or tenant isolation in v1.
- Replace TeamDynamix as the operational system of record.
- Build a real-time monitoring platform.
- Use member-level metrics as simplistic performance rankings.

## Assumptions and Known Inputs

- TeamDynamix access details are stored in the local `.env` file.
- Survey responses are available through the TeamDynamix reports endpoint using
  report ID `100482`.
- Report `100482` includes a stable `TicketID` field that can be used to join
  survey responses back to ticket records in the curated model.
- The system will use the TeamDynamix Web API for operational data acquisition.
- The primary user is the CIO only, so v1 can optimize for executive analysis
  rather than access partitioning.

## Recommended Approach

Use a notebook-first analytics workspace with a local cache and generated HTML
artifacts.

Why this approach:

- It fits the stated Jupyter-based workflow.
- It supports fast iteration on metrics and executive questions.
- It keeps the data model reusable without requiring a heavier analytics
  platform.
- It allows high-quality shareable outputs without introducing a live web app.

Alternatives considered:

1. A more formal warehouse-first analytics stack.
   This would scale better long term but adds unnecessary upfront structure for
   a single executive user.
2. A report-centric approach that relies mostly on TeamDynamix reports.
   This would be faster for narrow outputs but weaker for deep joins,
   consistent metrics, and drill-down analysis.

## System Overview

The system has four layers:

1. Ingestion layer
   Pull data from TeamDynamix APIs and selected TeamDynamix reports into a
   local cache.
2. Storage and modeling layer
   Preserve raw snapshots and build curated analytic tables for consistent
   metrics.
3. Analysis layer
   Provide Jupyter notebooks for executive exploration and root-cause analysis.
4. Publishing layer
   Render Tailwind CSS HTML artifacts for executive scorecards and the weekly
   leadership report.

This separation is intentional. Raw data acquisition, metric logic, and output
presentation should not be tightly coupled. The design must allow metric
definitions to evolve without requiring full re-ingestion and must ensure the
same numbers appear in notebooks and rendered reports.

## Data Acquisition Design

### Source Types

The system will ingest from two source patterns:

1. TeamDynamix operational API entities
   Used for tickets, people, groups or teams, accounts, services, and related
   metadata.
2. TeamDynamix report-backed datasets
   Used for survey responses and any other cases where Report Builder provides
   the cleanest extraction path.

### Authentication

- Read credentials and environment settings from `.env`.
- Use TeamDynamix-supported token-based authentication.
- Keep authentication logic isolated in a reusable client module.

### Incremental Refresh

The ingestion workflow should support incremental sync wherever possible by
using created, updated, responded, or closed date windows from TeamDynamix
search endpoints. Full refreshes should remain available for recovery or schema
changes, but normal operation should default to incremental refresh to reduce
API load and improve speed.

### Local Cache Expectations

The local cache must support:

- repeatable notebook analysis
- historical trend analysis across refresh runs
- quick regeneration of HTML artifacts
- recovery from transient API outages

The cache should preserve both raw records and curated tables. Raw records are
needed for auditability and reprocessing. Curated tables are needed for stable
notebook and report logic.

## Data Model Design

The curated layer should normalize and relate these analytical domains:

- tickets
- ticket events or history where available
- teams
- members
- services
- requestors or business units where appropriate
- surveys
- calendar periods

Key modeling requirement:

Survey rows must join to tickets using `TicketID` from report `100482`. Team,
service, assignee, and other organizational context should then be derived from
the ticket-side model rather than treated as independent survey-side join keys.
This keeps ticket data as the authoritative operational context.

The design should also support derived period tables such as week, month,
rolling 30 days, and prior calendar month because those windows will be used
repeatedly across notebooks and executive reports.

## Scorecard Design

The executive balanced scorecard will cover four areas.

### 1. Service Outcomes

Primary questions:

- Are users getting timely and effective service?
- Is backlog becoming harder to close?
- Are service levels stable or degrading?

Metrics:

- ticket intake volume
- closure volume
- open backlog size
- backlog aging distribution
- time to first response
- time to resolution
- SLA attainment
- reopen rate
- survey satisfaction
- survey response rate

Hard v1 commitments:

- ticket intake volume
- closure volume
- open backlog size
- backlog aging distribution
- time to first response
- time to resolution
- SLA attainment
- reopen rate
- survey satisfaction

Availability-dependent in v1:

- survey response rate, if a reliable denominator is available

### 2. Demand and Flow

Primary questions:

- What types of work are entering the system?
- Are teams keeping up with demand?
- Where is work becoming stuck or noisy?

Metrics:

- inflow versus outflow over time
- category mix
- service mix
- seasonal patterns
- spike detection
- assignment churn or reassignment counts
- unassigned queue volume
- handoff frequency where available

Hard v1 commitments:

- inflow versus outflow over time
- category mix
- service mix
- assignment churn or reassignment counts
- unassigned queue volume

Availability-dependent in v1:

- seasonal patterns
- spike detection
- handoff frequency

### 3. Team Effectiveness

Primary questions:

- Which teams are healthy, strained, or unstable?
- Where are backlogs and aging patterns concentrated?
- Where is delivery dependent on too few people?

Metrics:

- throughput by team
- team backlog size and aging
- SLA attainment by team
- reopen rate by team
- satisfaction by team
- concentration risk
- escalation burden
- distribution of work across staff

Hard v1 commitments:

- throughput by team
- team backlog size and aging
- SLA attainment by team
- reopen rate by team
- satisfaction by team
- distribution of work across staff

Availability-dependent in v1:

- concentration risk
- escalation burden

### 4. Staff Sustainability

Primary questions:

- Is work distributed sustainably?
- Are certain staff members carrying hidden escalation or interruption burden?
- Are signs of burnout risk visible in ticket patterns?

Metrics:

- assigned load
- work in progress
- closures over time
- reassignment burden
- interrupt-heavy work patterns
- after-hours activity if timestamps support it
- concentration of complex or high-priority work
- reliance on key individuals

Hard v1 commitments:

- assigned load
- work in progress
- closures over time
- reassignment burden

Availability-dependent in v1:

- interrupt-heavy work patterns
- after-hours activity
- concentration of complex or high-priority work
- reliance on key individuals

Important interpretation rule:

Individual metrics are leadership diagnostics, not simplistic rankings. The
system must avoid encouraging shallow interpretations such as "most tickets
closed equals best performer" without accounting for ticket type, severity,
reassignments, workload mix, or queue context.

## Survey Analytics Design

Survey data will be ingested from TeamDynamix report ID `100482` through the
reports endpoint.

The minimum required schema for v1 is:

- `ResponseID`
- `TicketID`
- `SurveyRequestedDate`
- `SurveyCompletedDate`
- at least one structured satisfaction answer field
- any free-text comment field if present

Useful supporting fields already observed in the report include:

- `ItemTitle`
- `SurveyCompletedFullName`
- `ItemCompletedFullName`
- `AccountName`

`TicketID` is the canonical join key. Team, service, and assignee rollups
should come from the ticket model after the survey rows are joined.

The survey analysis layer should support:

- response volume over time
- average score and score distribution
- low-score concentration by team
- low-score concentration by service or category
- assignee-linked patterns where appropriate
- free-text comment analysis if comments are present in the report
- correlations between dissatisfaction and operational factors such as long
  resolution time, repeated reassignment, or old backlog

Response rate is availability-dependent in v1. It should be calculated only if
the implementation can identify a reliable denominator for survey invitations or
eligible tickets from TeamDynamix data. If that denominator is not available
through the report or operational sources, v1 should expose response volume and
clearly mark response rate as unavailable rather than inventing an estimate.

Free-text comment analysis in v1 should remain lightweight. The goal is simple
qualitative summaries, notable themes, and highlighted representative comments
when appropriate, not a heavy NLP or classification project.

Survey results should be treated as a first-class scorecard signal and included
in both notebooks and HTML outputs.

## Output Design

### Jupyter Notebooks

The first version should include notebooks for:

- organizational health overview
- team-level scorecards and drill-downs
- member-level analysis
- survey analysis
- ad hoc investigation patterns for exceptions and outliers

Notebook outputs should favor reproducibility. Core metric calculations should
live in reusable Python modules or shared notebook utilities rather than being
reimplemented independently in each notebook.

### HTML Artifacts

The system should generate HTML artifacts styled with Tailwind CSS.

Initial HTML outputs should include:

- executive organization scorecard
- team scorecards
- survey summary views
- weekly executive report for leadership

These artifacts are generated snapshots, not a live web application. The
purpose is executive readability and easy sharing, not interactive hosting.

### Weekly Executive Report

The weekly report is a structured HTML artifact for the CIO's boss.

Rules:

- It runs weekly.
- It always analyzes the previous full calendar month relative to the run date.
- It presents charts first and commentary second.
- It should read like an executive dashboard pack with concise narrative
  interpretation following the visuals.
- The default comparison baseline for trend calls is the immediately preceding
  full calendar month. For example, a weekly report run in March 2026 analyzes
  February 2026 and compares it to January 2026.
- Multiple weekly runs during the same calendar month should intentionally
  regenerate the same prior-month analysis window, using refreshed cached data
  and updated commentary if the underlying source data has changed.

Required sections:

- executive summary scorecard
- major trend changes
- team improvements and deteriorations
- survey highlights
- notable risks or exceptions
- leadership attention items

The commentary should be concise, evidence-based, and tied directly to charted
findings.

## Architecture and Component Boundaries

The implementation should separate responsibilities into small, clear units:

- API client for TeamDynamix authentication and requests
- ingestion jobs for operational entities
- ingestion jobs for report-backed datasets
- local cache and storage utilities
- transformation and metric modules
- notebook helpers
- HTML rendering layer
- report orchestration layer

Each component should have one clear purpose, predictable inputs and outputs,
and independent test coverage where practical.

## Error Handling and Operational Expectations

The system should handle:

- expired or invalid credentials
- transient API failures
- partial sync failures
- report endpoint failures
- schema drift in report outputs
- empty or missing survey data for a period

Operational behavior expectations:

- fail clearly with actionable errors
- preserve previously cached data unless replacement succeeds
- validate required fields during ingestion
- log refresh results and record refresh timestamps
- make it obvious when an HTML artifact is based on stale data

## Testing Strategy

Testing should exist from the first implementation iteration.

Coverage should include:

- API client behavior with mocked responses
- incremental sync logic
- report ingestion for survey report `100482`
- transformation logic for curated datasets
- metric calculations for scorecard measures
- report window calculations for prior-month reporting
- HTML generation smoke tests

Metrics such as response rate, handoff frequency, after-hours activity, and
free-text comment analysis should be tested as availability-dependent features
when their source data is optional or environment-specific.

The design assumes the implementation will prefer automated tests over manual
verification wherever possible, while still allowing manual notebook inspection
for presentation quality.

## Key Risks

- Misleading metric interpretation if volume-based measures are overemphasized.
- Survey report schema changes breaking joins or trend analysis.
- Incomplete operational history if incremental sync boundaries are defined
  poorly.
- Overly broad first-version scope if too many report types or visual formats
  are added before the core scorecard is stable.

## Initial Deliverables

- Local TeamDynamix authentication and ingestion setup using `.env`.
- Cached operational datasets from TeamDynamix APIs.
- Cached survey dataset from report `100482`.
- Curated analytic tables for tickets, teams, members, services, and surveys.
- Jupyter notebooks for executive and drill-down analysis.
- Tailwind CSS HTML artifact generation.
- Weekly previous-month executive report with charts first and commentary
  second.

## Planning Readiness

This spec is ready for implementation planning. The next planning step should
translate the design into a concrete build sequence that establishes data
ingestion and verification first, then curated metrics, then notebooks, then
HTML artifact generation.
