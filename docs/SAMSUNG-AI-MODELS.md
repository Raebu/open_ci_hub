# Samsung Galaxy Book Go: private and hosted AI options

Validated on Debian 13 ARM64, `samsung-book-go`, 28 October 2026.

## Local model

- Qwen3-0.6B-Q4_K_M GGUF (SHA-256: `ac2d97712095a558e31573f62f466a3f9d93990898b0ec79d7c974c1780d524a`) runs through llama.cpp ARM64.
- The `oci-samsung-qwen.service` system service listens **only on `127.0.0.1:8878` on the Samsung**. No public or tailnet-wide AI endpoint is enabled.
- On the Samsung, choose **Open CI Hub · Local Qwen** in the applications menu. Its browser interface supports gzip. A plain `curl /` request lacking `Accept-Encoding: gzip` may receive HTTP 415; `curl --compressed http://127.0.0.1:8878/` returns the web UI.
- `GET /health` and `POST /v1/chat/completions` were tested successfully. The model can generate non-thinking responses using `/no_think` and an appropriate template setting.
- Service owner: restricted `ociworker`, systemd `MemoryMax=2100M`, `CPUQuota=650%`, restart on failure; enabled at boot.
- Open CI Hub's separate `open-ci-edge-worker.service` is an approved, restricted ARM64 worker with its own credential. Tested with a genuinely new `json.inspect` job and independently checked result hash.
- Debian and these system services remain active behind XFCE screen lock. System sleep/hibernate targets have been masked; power loss, thermal shutdown, phone disconnection and Android tether resets still interrupt service.

## Hosted, manually operated browser options

Both are linked in Open CI Hub's **AI models** dashboard section and installed in the Samsung user's applications menu:

- Hugging Face Chat: https://huggingface.co/chat/
- Google Gemini: https://gemini.google.com/

Hosted models use the provider's compute rather than the Samsung CPU/GPU for inference, **but browsers still use local RAM and bandwidth**. Free-tier availability, model selection, response times, account sign-in and usage limits are provider-dependent and subject to change. Browser chat access does **not** authorise automated scraping, submitting prompts from Open CI Hub, or access to the provider's API. Do not send client data or internal secrets without provider/privacy approval.

The shortcuts only open these sites when manually clicked; neither logs in nor runs a browser in the background.

## GPU and network limitations

- Adreno 618 DRM render nodes and Mesa Freedreno/Turnip are present, but Vulkan enumeration currently fails with `VK_ERROR_INITIALIZATION_FAILED` because the required signed Samsung Qualcomm firmware `qcdxkmsuc7180.mbn` is absent.
- `vulkaninfo` shows **llvmpipe CPU software rendering**, not a working compute GPU. Do not label this as GPU inference or attempt unsafe third-party firmware flashing.
- Recover the exact signed firmware from a verified OEM driver package, stage and validate non-destructively, and only then test Vulkan and a small offloaded model with rollback controls.
- The Samsung uses S22 USB tethering (`usb0`) and Tailscale private management. Android may need its tether mode re-enabled after reconnect/reboot.
- No automatic Google or Hugging Face credentials have been installed or requested.

## Verification quick reference

```bash
systemctl is-active open-ci-edge-worker oci-samsung-qwen tailscaled
systemctl is-enabled suspend.target hibernate.target
curl -fsS http://127.0.0.1:8878/health
curl -fsS --compressed http://127.0.0.1:8878/ | head -c 80
```

The model's loopback endpoint is intentionally unavailable from other machines without an explicitly authorised SSH tunnel. Avoid making the service bind to `0.0.0.0` or starting Tailscale Funnel.
