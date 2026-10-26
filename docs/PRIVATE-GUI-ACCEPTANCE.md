# Private graphical missions and owner acceptance

The existing Open CI Hub background mission engine can now run an
owner-submitted browser step using the private Xvfb display :91. It does not
read, move or control windows in KDE. There is no new agent or repository.

To opt into graphical mode, set display to private_xvfb inside the browse
step's input. Existing missions without this field remain headless. All
hostname restrictions, action allowlists and resource limits continue to
apply. An owner-approved mission is still required before the queued browser
step can execute. Chromium is launched non-headless in its existing restricted
bubblewrap namespace and receives only the X91 socket and private X11
authority, never the KDE socket or a home directory mount.

One example plan (save as a private JSON file within the repository):

    {
      "id": "private-visual-demo", "hosts": ["example.com"],
      "limits": {"max_cost_usd": 0, "max_seconds": 45,
                 "allow_browser_interaction": false},
      "steps": [{"id": "capture", "kind": "browse", "needs": [],
        "input": {"url": "https://example.com/",
                  "display": "private_xvfb",
                  "actions": [{"type": "screenshot"}]}}]
    }

Use the existing owner-approved mission submission workflow. An actual
1280x720 Chromium run through private Xvfb captured a public site screen
on Garuda. Separately, the encrypted GitHub session was authenticated in a
read-only headless browser test. Neither demonstrates unrestricted AI
reasoning, autonomous GUI planning, or password/MFA renewal.

Samsung and S25 have not been enrolled. Tailscale reported the S25 offline
and no Samsung present on 26 October 2026. Source-level fleet tests are not
physical job receipts. Cloud £0 billing verification is also not connected;
unknown cloud costs must remain denied. Independent third-party security
review requires an external assessor, not this same local suite.

The local authenticated GitHub CLI works without exporting any credentials,
so use scoped GitHub API access for repository work rather than sharing
personal browser session cookies.
