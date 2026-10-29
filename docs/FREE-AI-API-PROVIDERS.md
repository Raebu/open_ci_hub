# Open CI Hub · optional no-cost and trial AI APIs

Provider public documentation reviewed 29 October 2026. On 29 October all four owner-provided keys were authenticated and each provider completed a one-shot model inference with visible text. Activation records are stored privately on the Razer, with no keys in Git. Billing tier cannot be proved with an API key alone.

## Provider setup and restrictions

| Provider | Key console | Default manual test model | Status |
|---|---|---|---|
| Google AI Studio | https://aistudio.google.com/apikey | `gemini-3.1-flash-lite` | Free tier may be offered, limits vary by project; free data may be used for product improvement |
| NVIDIA NIM | https://build.nvidia.com/ | `nvidia/nemotron-3-ultra-550b-a55b` | One-shot prototype test passed; production/commercial permissions remain restricted |
| OpenRouter | https://openrouter.ai/settings/keys | `openrouter/free` | Free variants limited, typically 50/day and 20/minute on an unfunded account; no paid fallback |
| Groq Cloud | https://console.groq.com/keys | `openai/gpt-oss-20b` | Free developer-plan limits differ by model/organisation |

Official references:
- Google limits and pricing: https://ai.google.dev/gemini-api/docs/rate-limits and https://ai.google.dev/gemini-api/docs/pricing
- Gemini's OpenAI-compatible API: https://ai.google.dev/gemini-api/docs/openai
- NVIDIA NIM catalog and trial terms: https://build.nvidia.com/ and https://forums.developer.nvidia.com/t/nim-question/364032
- OpenRouter free model policy: https://openrouter.ai/collections/free-models
- Groq Cloud rate limits: https://console.groq.com/docs/rate-limits

**No exact RPM/TPM promise is hardcoded**. Quotas, models and free-plan eligibility depend on each provider, account, project and regional availability. Do not assume consumer free-tier permissions cover all commercial workloads.

## Owner-only local CLI (Razer)

All registration, key storage and tests are **opt-in** and run as the ordinary owner account, not the CI worker.

```bash
cd /mnt/kingston/home/theraeburngroup/Code_Projects/open-ci-hub
python3 cloud_ai_providers.py status
python3 cloud_ai_providers.py add-key gemini
python3 cloud_ai_providers.py add-key openrouter
python3 cloud_ai_providers.py add-key groq
# Only after checking the relevant account really has free API access:
python3 cloud_ai_providers.py test gemini --confirm-free-account
```

The `add-key` command prompts invisibly in a **local interactive terminal**; no API keys are passed in shell arguments, copied into ChatGPT, saved to the Git repository, shown in the dashboard or sent to the Samsung. The keys are held under `~/.config/open-ci-hub/cloud-ai-keys/`, directory mode 0700 and files mode 0600. Existing secrets cannot silently be overwritten.

The `test` command makes **one** HTTPS request, with a fixed allowlisted endpoint/model, a short fixed prompt and 32 output-token maximum. It never retries, redirects, changes provider or selects paid fallbacks. It refuses to run until the owner confirms that the relevant account/model is currently free. NVIDIA's Nemotron 3 Ultra developer endpoint has now been tested successfully; its usage remains limited to authorised developer prototyping, not unattended commercial production. A valid credential alone does not mean a free tier or automatic access exists.

## Activation verified 29 October 2026

The following four models completed authenticated, tiny output tests: Google `gemini-3.1-flash-lite`, NVIDIA `nvidia/nemotron-3-ultra-550b-a55b`, OpenRouter `openrouter/free` and Groq `openai/gpt-oss-20b` (Groq with low reasoning effort). NVIDIA, OpenRouter and Groq replied `READY`; Gemini replied `READY.` All tested connections are marked `activated: true` in the private owner-only activation file at `~/.config/open-ci-hub/cloud-ai-keys/activation.json` (0600). No API keys, prompts or billed amounts are recorded there.

The activation record means **manual owner-controlled API access is verified**, not that arbitrary Open CI Hub jobs can choose cloud inference, that commercial workloads are authorised, or that a paid billing account can never be charged. Google and Groq billing status cannot be established solely from an API key. All future tests require an explicit free/trial confirmation and fixed small token allowance; no paid fallback or automatic requests are configured.

Manage activation:

```bash
python3 cloud_ai_providers.py status
python3 cloud_ai_providers.py disable gemini    # change provider as required
python3 cloud_ai_providers.py activate gemini --confirm-free-account
```

The dashboard labels these as **last verified**, not an automatically refreshed account/usage report. Adding a live dynamic status route was left untouched because the existing protected backend source could not be safely edited through the authorised file editor. Its existing security controls remain intact.

## Open CI Hub and Samsung menu

The dashboard **AI models** section contains API setup links alongside the local Qwen and browser-only providers. The Samsung adds four user-clicked menu shortcuts to official developer consoles. Opening a console does not create a key, sign in, issue an API call or enable background automation.

**Current rollout is optional manual connection capability, not an unattended production provider router.** No autonomous task may select a cloud model, transmit confidential files, use account credits, or charge a paid tier through this module.

The Samsung Qwen service remains private on loopback and the approved ARM64 CI worker remains independent. Consumer browser apps and developer APIs are intentionally shown as different capabilities.
