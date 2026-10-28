# KDE desktop input repair — 28 October 2026

KWin does not implement wtype's virtual-keyboard protocol. The existing owner-enrolled ydotool service provides a compatible fallback. Text is passed literally via stdin, bounded to ASCII for this backend; unsupported Unicode fails explicitly. Key chords use Linux key codes and release modifiers in reverse order.

On this host, ydotool absolute mouse movement landed at the corner or overshot. KWin was using adaptive acceleration for the ydotool virtual device. Its profile was changed to flat using the existing KWin device property (no physical mouse/touchpad settings changed). The preference is persisted in kcminputrc under the ydotool virtual device. Separate corner reset and target relative motions, with compositor processing time before the click, now reach screenshot coordinates reliably on the current single 1920x1200 display.

Verified live: selected the UKDI browser tab, Stage 1 service, Security Open Call Cycle 2 and NPSA; entered title, abstract and Strategic Fit; used Page Down and dropdowns. UKDI saved Summary under draft ACC4236326. This is not a submitted application.

24 focused unit tests pass, including fallback modifier release, literal text transport, and separated pointer events. The local policy remains authoritative: every action still calls _require_desktop. Desktop control was disabled externally at 13:23 GMT during the task; no subsequent interaction was attempted after the permission denial. Owner confirmation is pending before reenrolment. Restart the tunnel runtime after review to load the repair in an existing long-lived MCP process.
