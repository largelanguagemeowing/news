# Pipeline Architecture

The pipeline turns RSS feeds into deduplicated, labeled events and exports them
as JSON for a static dashboard. The code is the source of truth for *how* it
works — entrypoints, stages, tables, and configuration can all be read from the
repo. This document records only what the code cannot tell you: the *why*
behind the pipeline's shape, and the constraints those decisions respond to.
Read it before changing pipeline behavior when you need the reasoning behind a
structure.

## Three runs, not one monolith

The pipeline is split into separate runs — fetch, enrich, classify — each with
its own schedule, plus a combined run for back-to-back execution on demand. The
split exists because the passes have very different economics:

- **Fetch** is cheap and frequent: it enqueues articles from feeds.
- **Enrich** is expensive and quota-bound: it pulls article bodies through
  external extraction APIs that rate-limit and cost money.
- **Classify** is discrete and retrainable: it clusters and labels, and improves
  as training data grows.

Separating them keeps each run short, limits the blast radius of a failure, and
lets each pass retry independently. Runs are allowed to overlap rather than
cancel each other, so a long classify run does not interrupt the feed schedule.

## The runs share one orchestration envelope

Separate runs must not mean separate run bookkeeping. The three entrypoints
share a single run envelope (`app.jobs.runner`) that owns the run record, the
stage records, and incident escalation, because those policies have to be
identical across runs to stay trustworthy. They were copy-pasted once and
drifted — enrich silently stopped escalating failures while fetch and classify
kept opening incidents — so the envelope exists to make the policy single
source of truth rather than a thing each run re-implements. A pipeline is just
a named list of stages; the envelope records, times, and escalates them the
same way everywhere.

## Enrichment is a separate pass because bodies are the expensive part

Ingest only needs titles, URLs, and metadata to deduplicate. The body is fetched
afterwards in a bounded, batched pass, so a transient extraction failure does
not block ingest, and the expensive work happens once per article rather than
once per feed refresh.

## Body extraction is a preference chain, not a fallback stack

Several extractors exist for the same job. They are ordered by the quality of
their output, and the chain stops at the first success. Rich markdown-oriented
extractors are favored for content that publishes well, but they are
quota- and rate-limited, so quota state is persisted and honored between runs.
A dependable general extractor is the guarantee that every article still gets a
body when the quality extractors are exhausted or unavailable. `defuddle` is an
experimental extractor and participates only when explicitly enabled.

The chain is implemented as a registry keyed by method name (`EXTRACTORS`):
each extractor is one attempt function plus one registration line, and the
ordering policy (`_extraction_methods`) is separate from each method's own
behaviour. Adding an extractor no longer means editing a nested method dispatch;
an extractor's quota, breaker, and fallback logic live with that extractor.

## Daily quotas are one machine, not two copies

The two cloud extractors that run on a paid daily budget (markdown.new,
compress.new) share the same bookkeeping: one quota JSON state file per
extractor, a one-UTC-day period, reserve-before-request, record-after-response,
and exhausted-when-requests-reach-limit. That logic has to be identical for both
or the budgets drift apart, so it lives once as `DailyQuota` with two instances
rather than as two hand-rolled copies (which is what they were, until they
diverged in how a failure was recorded).

## The ingest seam crosses only real adapters

`ingest_stage` used to take ~13 callables as parameters, but most of them
(`parse_date`, `iso`, `canonicalize_url`, `normalize_text`, `sha1_hexdigest`,
`simhash64`, ...) have exactly one implementation — threading them only created
bindings for the caller to reconcile. Those are now imported at module scope
inside the stage, and the interface takes one `IngestContext` object carrying
the adapters that genuinely have a second implementation: the enrichment
adapter (no-op in the fetch pipe vs the full chain), the cooldown policy, and
the incident client. Tests can still swap any of it by monkeypatching the
module or passing a different context; callers no longer restate seventeen
arguments at every call site.

## Classification is ML-first with a rule-based guarantee

Events get labels from an ML model when it produces a result; otherwise a
deterministic rule set labels them. The rules are not a last-ditch fallback but
a *guarantee*: every event is labeled, decisions are explainable, and the rules
generate weak labels used as training data for the model. The model sharpens
classification where rules are blunt; the rules ensure an absent or
low-confidence model never leaves an event unlabeled.

## Events are a deterministic rebuild

Clustering groups duplicate and related coverage into events using title
similarity plus a content hash, within a time window. The rebuild is
deliberately destructive-but-atomic: it clears and recomputes events from the
current articles so events can never drift from the articles they reference,
and a failed run rolls back entirely rather than committing a partial rebuild.
The similarity threshold is a quality-vs-noise tradeoff — too loose merges
distinct stories, too strict splits one story across events — so it is
configuration, tuned by inspecting real exports, not a scientific constant.

## Export derives; it does not store

Status files are derived from the database at export time instead of from
long-lived counters. Mutable counters drifted (never reset, never decayed) and
painted the dashboard with stale numbers; at this scale, deriving them from the
source tables on each export is both accurate and cheap.

## The audit tables exist because silence was ambiguous

Ingest and enrichment write attempt logs, and failed entries go to a dead-letter
record. They exist because silently dropping an entry made "feed is stale"
indistinguishable from "everything was a duplicate" or "parsing failed". The
logs turn operational silence into diagnosable history.

## Failures land where the reviewer already looks

Pipeline failures escalate to GitHub Issues instead of disappearing into logs,
and incidents are surfaced in the exported status. The pipeline's operating
position is: a problem becomes a record the dashboard already shows.

## The repo is part of the state machine

Exported status is committed back to the repository by the artifact jobs. The
deployed dashboard is a static snapshot of that data — there is no backend —
and the git history doubles as a record of what was served and when.

## pipeline.py is a facade, not the pipeline

`app/jobs/pipeline.py` used to be a ~950-line grab bag: the settings snapshot,
the source-id migrations, the circuit breakers, the parse adapters, the whole
extraction chain, and the export facade all shared one import surface. That
pulled unrelated concerns together and pushed tests into monkeypatching
re-export wrappers that existed only because the chain happened to live there.

That logic now lives in its owner modules — the settings snapshot in
`app.settings`, migration replay in `app.jobs.migrations`, extraction policy
and the chain in `app.jobs.enrichment`, the real export stages in
`app.jobs.stages_export` — and `pipeline.py` keeps only what the pipeline
genuinely owns (date parsing, the cooldown/auto-disable policy, source
upserts, the classify wrapper, the export legs) plus a re-export surface for
call sites that still name it. `DEFUDDLE_ENABLED` is a mutable binding in
`enrichment.py`, the module whose chain reads it, so the backfill
`--enable-defuddle` override reaches the code it gates. New code should import
from the owner modules directly; tests patch the real modules, not the
facade.