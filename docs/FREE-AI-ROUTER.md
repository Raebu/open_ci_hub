# Free-only automatic AI model routing — 29 October 2026

## What is live

- The model router is in free_ai_router.py on the Razer.
- A user-systemd timer runs every 15 minutes: open-ci-hub-free-ai-monitor.timer. Initial run passed. User-systemd linger is enabled.
- The monitor makes read-only requests to provider model catalogs, no inference or paid API requests.
- OpenRouter openrouter/free is eligible only with published 0 input and 0 output price. An additional OpenRouter provider.max_price guard is fixed at zero.
- NVIDIA Nemotron 3 Ultra is eligible only for owner-authorised public developer-prototyping tasks, not commercial production.
- Gemini and Groq have valid keys and previous one-shot inference tests, but cannot be used unattended until the user verifies the specific account is free-tier with paid billing disabled. Such owner attestations expire after 24 hours.
- Local Samsung Qwen 0.6B is the fallback when no permitted cloud model returns text. Private tasks always use local Qwen regardless of cloud flags.

## Calling the router (fish shell)

From the Open CI Hub repository:

    cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
    python3 free_ai_router.py status

Automatic task classification for a public non-confidential task (free cloud permitted):

    printf 'Explain a Python function in one sentence.' | python3 free_ai_router.py ask --data-class public --allow-cloud

Allow developer-prototyping NVIDIA selection for a public task:

    printf 'Explain a pure function in one sentence.' | python3 free_ai_router.py ask --purpose prototype --data-class public --allow-cloud

Internal/private task, always restricted to Samsung:

    printf 'Summarise a private memo.' | python3 free_ai_router.py ask --data-class private

The router chooses the task type automatically, or accepts --task coding, reasoning, planning, analysis, extraction, translation, classification or chat. It never takes the prompt from a shell command argument; only stdin.

## Free-only and recovery controls

- Read-only monitor refreshes every 15 minutes. Catalog checks become stale after 45 minutes: cloud use is suspended until a successful check.
- Actual 401, 402, 403 or 429 failures initiate a one-hour cooldown; no repeated inference probes to test availability. After the cooldown, the router can re-enable a still-verified free endpoint.
- Locally reserved request ceilings: OpenRouter 12/day, NVIDIA 8/day and Gemini/Groq 6/day only if freshly free-account attested. Minimum 75 seconds between provider attempts.
- No paid fallback, no unrestricted background cloud traffic, no automatic submission of internal code or client data.
- Request prompts limited to 6,000 characters and generated output limited to 256 tokens.
- Metadata, attempt counters and cooldowns are owner-private under ~/.local/state/open-ci-hub/free-ai-router. API keys remain separately private under ~/.config/open-ci-hub/cloud-ai-keys.
- The monitor can restore a zero-priced OpenRouter endpoint automatically when pricing/availability becomes valid again. It cannot establish Gemini/Groq billing tiers from model listing or API-key authentication alone.

## Free account verification (only after checking each provider console)

If Gemini or Groq shows a free-tier project/organisation with no paid billing enabled:

    python3 free_ai_router.py verify-free-account gemini --confirm-free-tier --confirm-no-paid-billing
    python3 free_ai_router.py verify-free-account groq --confirm-free-tier --confirm-no-paid-billing

The temporary proof expires after 24 hours and must be refreshed. Do not use these flags if paid access or charges are possible.

## Audit and verification

    systemctl --user status open-ci-hub-free-ai-monitor.timer
    systemctl --user list-timers --all open-ci-hub-free-ai-monitor.timer
    journalctl --user -u open-ci-hub-free-ai-monitor.service -n 30 --no-pager
    python3 -m unittest tests.test_free_ai_router

The systemd service source is in linux-worker/open-ci-hub-free-ai-monitor.service; the installed timer is at ~/.config/systemd/user/open-ci-hub-free-ai-monitor.timer.

Live validations: a private prompt returned READY from Samsung Qwen, developer reasoning was handled by NVIDIA Nemotron 3 Ultra, and a routine public request correctly fell back from an empty OpenRouter/free response to Samsung Qwen.

Important: Existing Open CI Hub business automations still need to deliberately call this scoped interface for eligible public tasks; they have not been granted access to transmit confidential project data or to use paid endpoints.
