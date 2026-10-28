# Native Razer Direct Client Acquisition and ChatGPT Handoff

Implemented 28 October 2026 in the EXISTING Open CI Hub repository.

## Architecture

Razer Garuda systemd user timer: open-ci-hub-business-execution.timer
  -> existing open-ci-hub-business-execution.service
    -> business_execution.py run, at :12 and :42 every hour
      -> existing canonical lead source and provider/source checks
      -> approved guarded outbound queue processing
      -> raeburn_sales_native.SalesFeed.build
        -> .local/business-execution/native-client-acquisition.json
        -> .local/business-execution/native-client-acquisition.sqlite

The native sales worker is a functional stage of the existing
systemd task, not an additional scheduler, CRM or email campaign.

The EXISTING ChatGPT Direct Client Acquisition & Cash Engine runs at
minute :05. It can pull the Razer's source-backed report through
Open CI Hub run_pc_command on every ordinary scheduled run:

    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 raeburn_sales_native.py handoff

This is a native-to-ChatGPT PULL through an existing automation, not
a claimed ChatGPT task push API. No new ChatGPT task is needed.
If the Razer sleeps, is offline, or the tool is unavailable, ChatGPT
continues its directly authorised Gmail/Resend work and marks native
source coverage incomplete.

## Status and privacy

- Read-only canonical Lead Finder snapshots; first run read 36 companies,
  111 contacts, 17 enriched company records and 36 requiring review.
- Private 0600 JSON handoff and 0600 event ledger. No names, phone,
  personal email addresses, OAuth tokens or message bodies copied out.
- Up to 12 internal company IDs with source-relevance checks remaining;
  no lead is declared legal/qualified for sending without full evidence.
- Latest send/readback/suppression status from existing local providers
  and encrypted send queue. Empty local inbox is NOT proof of no Gmail
  responses: native provider credentials must be separately authorised.
- Canonical sheet snapshot indicates freshness only; it does not
  constitute real external acceptance, money collection or new sale.
- Event IDs and a material-change fingerprint avoid repeated prompts
  when only the clock advances or the source is unchanged.
- A feed older than 3 hours is marked stale.
- New outreach sent, genuine human replies, meetings and cash collected
  remain unknown until authoritative provider readback; never fabricate.
- The outbound sending policy is approved, but the native sender still
  requires independent Gmail/Resend OAuth, canonical case ownership,
  verified full reply/suppression checks and legal qualification.

## Operating handoff

At each hourly ChatGPT Sales run, read the native report FIRST if
the Razer Open CI Hub tool is connected. If no live report is available,
continue direct authorised Gmail/Resend reconciliation and prospecting.
Never use this aggregate report alone as a reason to send a message.
Resolve contact case by internal ID against the canonical Lead Finder
and original Gmail/Resend threads, confirm source legality, company-wide
suppression and no prior send. Carry out buyer replies, due followups,
qualified outreach and meeting actions where authorised. Verify
provider receipt and actual sales/cash, not candidate counts.

The native worker intentionally cannot independently prove that a
contact's reply was read, a customer consented or an invoice was paid.
Those sources need authorised provider API credentials and a verified
canonical sync/cutover. The existing five ChatGPT scheduled engines stay
active until that happens.

## Operator commands on the Razer

    systemctl --user status open-ci-hub-business-execution.timer
    systemctl --user start open-ci-hub-business-execution.service
    python3 raeburn_sales_native.py status
    python3 raeburn_sales_native.py handoff
    python3 business_execution.py status

## Cutover

After end-to-end native credential, suppression, reply and send receipt
parity, the scheduled ChatGPT Sales task can be downgraded to a review
task or retired to free its slot. The existing ChatGPT COO task can
consume the same native report when suitable. Do not prematurely disable
current authorised direct-client acquisition or duplicate campaigns.
