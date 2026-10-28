# Raeburn business execution migration — operating specification

Status: PARALLEL SHADOW. This is an incremental migration, not a completed replacement of five hourly ChatGPT scheduled tasks. Do not pause the existing engines without provider receipts and acceptance.

## Existing engines mapped once

| Existing ChatGPT engine | Open CI Hub lane | Canonical source |
| --- | --- | --- |
| Direct Client Acquisition & Cash | sales | Existing BD Pipeline and email threads |
| Bid, Funding & Strategic Opportunity | funding | Existing bid register and My Tasks |
| Product, Delivery & Monetisation | product | Actual GitHub/GitLab repos, CI, release receipts |
| Portfolio, Ventures, Media & Social Impact | portfolio | Venture-specific records and My Tasks |
| COO, Inbox, Obligations & Control Tower | coo | My Tasks, inbox and authenticated evidence |

The five original ChatGPT task IDs and private Google Sheets URLs are kept
only in a local private 0600 config file, not on GitHub.

## Current source coverage

- Eight explicitly selected local repositories: Open CI Hub, LeadBomb,
  GIBP, GIBP app, RaeburnEmailer, Service_Business, TRG Recruitment
  and Raeburn Ventures website. The GitHub CLI reads open issues,
  pull requests and workflow-run evidence; a green GitHub CI does not
  imply independent review or approved production release.
- Existing Lead Finder at Find_rec_firms/data/find_rec_firms.db:
  SQLite opened READ-ONLY. Identity/contact rows stay in the source DB.
  The Hub keeps company IDs, provenance flags and review-stage metadata.
- Existing My Tasks Google Sheet: one authenticated owner Google Drive
  readback, cached privately with only row, business lane, priority,
  reported status and evidence-link presence; expires after 24 hours.
  No names, emails, source message bodies or secrets are copied.
- BD Pipeline Google Sheet: existing canonical pointer only; access from
  business identity returned 403. No new tracker or duplicate Sheet made.
- Selected existing tender/partnership canonical IDs are monitored without
  claiming an award, deal, application or correspondence was completed.

## What the Razer service performs

The normal desktop user runs a restricted reconciliation service twice
hourly. Each run:

1. Reads exact allowlisted repo checkouts with fixed read-only git commands;
   flags dirty, non-main, mismatched origin and protected checkouts.
2. Reads bounded GitHub issues, PRs and CI runs; compares CI head SHA to
   the current local revision. Never merges, deploys or executes issue text.
3. Reads the Lead Finder DB read-only and identifies companies needing
   source, Companies House and legal basis recheck. None is automatically
   deemed send-ready.
4. Checks existing canonical funding and partnership sources without
   interpreting claimed status as external proof.
5. Reads an expiring private Google Sheet source snapshot if available.
6. Upserts deduplicated execution metadata with source hashes and events in
   a private 0600 SQLite file, not a competing customer pipeline.
7. Produces a private source-backed briefing with actual issue and PR URLs,
   readiness gaps and next evidence gates.
8. Records actual external actions by this service as zero unless proven.

Use on the Razer:

    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 business_execution.py run
    python3 business_execution.py status
    python3 business_execution.py brief
    python3 business_execution.py readiness

The private output files reside under .local/business-execution.

## Safety and cutover gates

- Sales: require Gmail/Resend scoped credentials, verified full-thread
  reply reconciliation, suppression, dedupe, lawful recipient status,
  working hours and independent provider send/delivery receipts.
- Funding: authenticated actual tender packs, truthful eligibility,
  director-signature gate when appropriate, deadline and portal receipt.
- Products: isolated coding and test runners, existing repair_controller
  gates, CI at exact SHA, independent review, safe merge, rollback and
  live customer acceptance. Existing protected theraeburngroup.com
  domain and subdomain scheduled write exclusions stay in effect.
- Portfolio: verified partner thread and regulatory-claims controls.
  No false UK/Nepal banking authorisation or customer-fund assertions.
- COO: equivalent inbox, obligations, family and security coverage,
  canonical Sheet write/readback and timely exception escalation.
- No new subscriptions, cloud machines, outbound sends, payments,
  contracts, ChatGPT automation deletion or website changes.

This local service does not inherit ChatGPT plugin credentials. A one-time
Google Sheet cache is not a permanent authenticated connector. All five
ChatGPT tasks remain enabled: NO task slots have been freed so far.

## Install and persistence

After source is tested, committed and pushed to the existing main:

    python3 scripts/install-business-execution.py --confirm
    systemctl --user status open-ci-hub-business-execution.timer
    journalctl --user -u open-ci-hub-business-execution.service -n 20

The installer refuses other branches, unpublished/dirty code and unknown
existing units. Installs exactly ONE user timer and ONE oneshot service.
CPUQuota 25%, MemoryMax 256M, Nice 17, no new privileged account.
The Razer user is configured for lingering, but work cannot run while
the physical Razer is asleep or switched off. A verified Contabo migration
is a separate required acceptance gate, not silently assumed.

Disable the new shadow scanner only, without changing ChatGPT tasks:

    systemctl --user disable --now open-ci-hub-business-execution.timer

## Acceptance before replacement

A migration lane must demonstrate real provider readbacks: verified email,
meeting, accepted proposal or settled revenue; actual bid portal receipt;
trusted CI, merge and deployed service; real partner response; or COO
source and escalation parity. Mere issue discovery, source scans, prompt
changes, created draft or green CI do NOT count as external completion.

Run in parallel, compare deduplication, privacy, uptime, source freshness,
error recovery and real outcomes for a documented acceptance period.
Then disable one old scheduled task at a time, with owner approval and a
rollback plan. ChatGPT silence must never be treated as approval.

This implementation provides source-connected shadow execution and
inspectable evidence. It is not yet the fully autonomous revenue,
application submission or coding/deployment system the user ultimately
wants; those provider integration and trusted execution gates remain open.
