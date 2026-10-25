# Open CI Hub — opt-in public internet for local CI

**21 October 2026: Full public-Web mode implemented and security-tested.**

Open CI Hub has two network modes for each repository in the owner-controlled private
`.local/ci-bridge/manifest.json` (permissions 0600):

- `"network": "offline"` — **default**, job container uses `--network none` and has no egress socket
- `"network": "full", "trusted_network_confirmed": true` — all **public HTTP and HTTPS** sites via a dedicated, isolated gateway, for approved push builds only

**Full does not mean unrestricted TCP/UDP networking.** The user's chosen Full mode permits external web access (ports 80/443) useful for npm, PyPI, Composer, GitHub and registries. Arbitrary SSH, SMTP, game protocols, inbound connections and private/LAN addresses are deliberately unavailable to CI containers. Direct ChatGPT/owner desktop terminal already has normal PC internet access.

## Enforcement and trust boundary

1. The build container **always** retains Docker `--network none`, `--read-only`, nonroot UID, no added Linux capabilities, no new privileges, bounded CPU/RAM/PIDs and a digest-pinned image.
2. For a **trusted** repository and **push** event only, the owner-side runner creates a unique private Unix-domain socket below its 0700 user runtime directory. Only the current job receives a **read-only bind mount** of that socket directory.
3. A local **statically built C bridge** runs as the container entrypoint. It listens on the container's own localhost port 3128 and forwards only to that Unix socket. The original approved step argv is exec'd without invoking a shell or interpreting provider YAML.
4. The host-side Python proxy accepts HTTP proxy/CONNECT requests, resolves each name using the host resolver and pins the connection to verified **global public numeric IPs**. It rejects loopback, RFC1918, link-local, metadata, CGNAT, multicast, reserved, IPv4-mapped private IPv6, mixed public/private DNS answers and private DNS suffixes. It permits ports 80/443 only.
5. `HTTP_PROXY`/`HTTPS_PROXY` and lowercase variants are set for the job and `NO_PROXY` cleared. Network clients that bypass the proxy still fail because the container has **no network interface**; an application must use an HTTP proxy for egress.
6. The broker's existing thermal, memory, CPU and native-runner reservations apply unchanged. Full internet is **not** granted by webhooks, PR events or a repository's committed configuration file. Only a private manifest owned by the local operator may opt in.
7. There are no cloud/API credentials, host Docker socket, host filesystem roots or network namespaces mounted into a build container. The host proxy can make outgoing public requests but not requests to private networks. Outbound requests can still disclose build source or artefacts to public servers, so **do not approve untrusted repositories**.

### Configuration

For a trusted repository in the private manifest:

```json
{
  "network": "full",
  "trusted_network_confirmed": true
}
```

These are extra fields on a pre-existing repository entry, **not a standalone manifest**. The default is Offline; do not globally enable Full by changing Docker daemon networking.

After editing the manifest with an owner-controlled editor, run `python3 -c 'import ci_bridge; ci_bridge.load_manifest()'` to validate. Restart the `open-ci-hub-bridge-poll.service` and `open-ci-hub-bridge-worker.service` **only when no bridge job is leased**, because each process loads the manifest at start. The dashboard's **CI Internet** section shows each repository's effective mode but cannot weaken the private policy.

To return to Offline, set `network` to `offline` and remove `trusted_network_confirmed`, validate and restart the bridge services while idle.

### Evidence / verification

```bash
python3 -m unittest tests.test_ci_network tests.test_ci_bridge -q
PYTHONPATH=. python3 scripts/network_integration_smoke.py
python3 scripts/validate.py
```

The bounded Docker smoke test uses a preloaded Python 3.12 image with 256 MiB RAM, half a CPU and no host network. It verifies:
- HTTPS access to the public npm registry succeeds;
- loopback, laptop LAN, cloud metadata and 10.x private address requests through the egress socket all receive HTTP 403;
- direct public-IP connections inside the container remain blocked by Docker `--network none`.

The test is a **network isolation proof**, not an end-to-end completed CI job or a production release. Actual push→queue→container→provider commit-status evidence and live deployment verification are separate release gates.

### Limitations

- HTTP(S) package managers usually work with `HTTP_PROXY` and `HTTPS_PROXY`; tools that insist on raw sockets or custom ports will fail until explicitly reviewed.
- The proxy uses host DNS with public-IP pinning; any compromised public endpoint may still receive data. This is network segmentation, not a malicious-code sandbox guarantee.
- Proxy capacity, timeouts and per-job resources are bounded, but external dependency integrity still requires lockfiles, checksums, SCA and SBOM scanning.
- The C connector is compiled **on the authorised laptop** from reviewed source into a private per-version binary using `gcc -static`. A new host without this toolchain will fail closed.
- Never edit kernel firewall, expose Tailscale/LAN or mount the host Docker socket to give a CI job 'full internet'.
