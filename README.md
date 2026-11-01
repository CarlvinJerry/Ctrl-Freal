# Ctrl Freak

Self-hosted remote for Android TV / Google TV and LG webOS. The Raspberry Pi runs the server; the browser is the remote.

## Version

Current app version: **3.13.0**. `version.py` is the single source for the displayed version and bootstrap API. Use semantic versioning (`MAJOR.MINOR.PATCH`): increment major for incompatible releases, minor for backward-compatible features, and patch for fixes. Update `version.py` with each release.

## Add a TV

Open **Add TV** beside the TV selector. **Scan for TVs** searches from the Pi for Android TV Remote v2 and LG webOS devices. If discovery misses a TV, choose its type and enter its private IPv4 address manually. Turn the TV on before pairing.

- **Android TV / Google TV:** The TV must have Android TV Remote Service. Start pairing, enter the code shown on the TV, then finish. ADB is only needed for optional diagnostics and installed-app discovery.
- **LG webOS:** Enable the TV's mobile-app connection setting (the wording varies by model). Start pairing and accept the request on the TV using its physical remote. No numeric code is used. For wake from standby, also enable **Mobile TV On → Turn On via Wi-Fi** on the TV. Ctrl Freak saves the TV's network address when available and sends a wake signal while webOS is asleep.

For the **LJ570U (webOS 3.5)**, look under **Settings → All Settings → Network → LG Connect Apps** before pairing. The pairing popup repeats these steps. LG lists this model family with a built-in web browser and mobile-app support; actual compatibility will be verified on the TV during deployment.

The Pi needs `Flask`, `waitress`, `androidtvremote2`, `zeroconf`, and `aiowebostv` from `requirements.txt`. These are installed only during an approved deployment. Do not commit `tvs.json`, PIN/session files, pairing certificates, or webOS client keys. Newly paired TVs appear immediately in the selector; existing TV settings and credentials remain in place.

Discovery requires the Pi and TVs to share a network that permits mDNS and SSDP multicast. Manual IP entry remains available when discovery is blocked. Some remote buttons depend on the selected TV's protocol and firmware.

## MagicMirror

**MagicMirror** appears in Apps and can be added to Favorites. On Android TV, tapping it checks for the bundled `com.ctrlfreak.magicmirror` viewer, installs the signed APK through authorized ADB when needed, and opens the live page in immersive full screen. If ADB is unavailable, it opens the TV browser. LG webOS also uses its browser. The default address is `http://192.168.1.21:8080/`; set `REMOTE_MAGICMIRROR_URL` for a different reachable address. MagicMirror must listen on the Pi's LAN interface and allow the TV's IP. On older webOS 3.5 browsers, complex MagicMirror modules may need a simpler layout. Ctrl Freak does not restart or reconfigure the MagicMirror service. The viewer source and reproducible build script are in `android-viewer/`; preserve the Pi's private viewer signing key for future APK upgrades.

## Live TV information

Status refreshes every three seconds. On Android TVs that do not publish volume or foreground app through Android TV Remote v2, Ctrl Freak reads those values through ADB about every ten seconds. Device diagnostics and app discovery reconnect ADB when the TV is reachable and previously authorized. A newly opened phone selects a reachable TV when an older saved TV address is offline. Enable network ADB on the TV for these features and for automatic MagicMirror viewer installation.

Paired TVs and credentials stay in `tvs.json` on the Pi. Every two minutes, Ctrl Freak searches for disconnected saved TVs and updates their IP if the same TV has moved. Android TV discovery combines network announcements and local subnet checks, then verifies the existing pairing certificate. LG webOS discovery combines SSDP and local subnet checks, then matches the saved TV network address before updating its IP. The **Find saved TVs** button in Settings runs the same check immediately. **Refresh memory & storage** reads the selected TV's current RAM and storage values; those values also refresh every 30 seconds. TVs must be awake and reachable from the Pi for IP recovery. Both Wi-Fi bands must allow local device traffic; router isolation cannot be bypassed by the app.

## Household features

**Guest remote:** The owner creates a temporary six-digit PIN in Settings. It grants only volume, playback, and navigation controls. The PIN expires after the chosen duration (1–72 hours) or can be revoked immediately. Only a salted hash is saved in `.guest_access.json`; the PIN is shown once. Pairing, preferences, diagnostics, scenes, and other Settings endpoints remain owner-only.

**Shared preferences:** Theme, accent, haptics, favorites, and scenes are saved in `preferences.json` on the Pi and update across signed-in phones. The first phone to open the new version imports its existing browser preferences if the Pi has no preferences file. The selected TV remains local to each phone.

**Room scenes:** Create a scene for the selected TV in Settings with an app and/or target volume. Tap it on the Remote tab to run it. Android TV volume uses ADB's media-session command; LG webOS uses its own volume API. The scene checks the app is installed before launching it. Network ADB must be authorized for Android TV volume and app discovery.

**MagicMirror screensaver:** On Android TV, Settings can install the bundled viewer as a system screensaver and restore the previous choice. The viewer fits the 1920×1080 mirror content within the TV while its slideshow background fills the screen edge to edge. If the Pi is offline, the viewer shows a local full-screen clock and checks every 30 seconds until MagicMirror returns. Android does not allow this unprivileged viewer to switch to the TV's original system screensaver while the Pi is off. This setting uses authorized ADB and is unavailable on LG webOS. The TV's firmware controls when idle screensavers start; Ctrl Freak does not change the idle timeout.

**Jack Sparrow:** Its own tab searches configured Jackett public indexers, Internet Archive, and owner supplied RSS feeds. The indexer service is private to the Pi; its API key and download URLs stay in the server state. Results appear in a scrollable grid with type filters, ordering, language labels, and downloaded markers. English labelled results are prioritized, though unlabeled sources cannot guarantee language. The owner reviews each download, chooses a quality and Plex library, and Transmission downloads to the Pi. Finished files are linked or copied into the configured media folders for Plex. Plex series are checked against TVMaze for newly aired episodes; explicitly tracked Plex movies are checked for possible sequels and quality upgrades. Notices and dismissed IDs persist across devices. Jack imported movie files can be deleted from the Downloads section; deletion is limited to the recorded import paths. Sources can fail or be blocked independently. No download has been validated end to end on the Pi yet; the search and Plex checks have been validated.

**v3.7.0:** Android TV apps are discovered from their TV and standard launcher entries and opened directly through ADB; sideloaded launchable apps can appear in Favorites. Jack's active matches are grouped by episode, bounded in a phone-sized list, and hidden once queued or completed. Completed downloads are in a collapsed archive. The v3.6.0 release is tagged for rollback.

**v3.8.0:** The Pi caches app icons exported from the TV's installed packages by the MagicMirror viewer and serves them only to authenticated remotes. Text labels remain available. Jack displays download health, progress, speed and estimated time, and checks the local Plex library database to distinguish imported files from media ready in Plex. The Check Plex button refreshes this status; Plex still performs its own automatic or periodic scan. The TV may be off while the Pi downloads.

**v3.9.0:** TV app banners become full artwork cards where available; square app icons remain the fallback. Jack and Plex rows use locally cached, phone-sized Plex posters where titles match. A compact Pi health strip, Continue Watching shelf, unified search, grouped scrollable release notices and match options, collapsible app lists, and reduced-motion-aware transitions improve phone use. Jack keeps the last known job cards during transient status errors, offers a file verification action for failed torrents, and waits for an explicit search or Explore Latest tap before loading broad release results. Torrent-match pop-up toasts are disabled. Continue Watching opens Plex on the TV; title-specific playback remains a future integration.

**v3.10.0:** Room-specific appearance profiles add uploaded photo or Plex-artwork backgrounds, dim and blur controls, glass strength, and optional effects. Quick actions provide one-tap Plex, MagicMirror and downloads access. Jack previews storage before downloads and folds download controls into a detail sheet. Encrypted backup and restore carries paired TV credentials, room photos, preferences, and Jack sources; current downloads remain intact. A backup password of at least 12 characters is required. Keep the backup file and password secure.

**v3.10.1:** The version and builder signoff follows the selected accent color.

**v3.11.0:** TCL Android TV screensaver setup restores the MagicMirror viewer's auto-start permission and reports when the TV blocks it. The power button supports one tap for standby/wake, three taps for full Android shutdown, five taps for restart, and the existing long press for the TV power menu. Android TV debugging is required for shutdown and restart. A fully shut down TV may need its physical remote to start again.

**v3.11.1:** Three-tap shutdown and five-tap restart run directly without confirmation prompts.

**v3.11.2:** Touchscreen pointer-leave events no longer erase power tap counts. A tap count badge and immediate action message make the gesture visible. One-tap power uses ADB when available so standby TVs can wake even while the paired remote service reconnects. Android TV shutdown and restart reconnect ADB before sending their commands.

**v3.12.0:** LG webOS power uses the live webOS shutdown command and Wake-on-LAN for power-on. The Pi remembers the LG TV's network address while it is connected; LG's Mobile TV On / Turn On via Wi-Fi setting must be enabled. The MagicMirror Android viewer fits its own WebView to the TV viewport instead of assuming 1920×1080. The original MagicMirror server layout remains independent of the viewer.

**v3.12.1:** The Pi's local MagicMirror Electron window reopens when the HDMI display changes resolution after the LG wakes. This keeps the original mirror layout responsive without changing its server CSS or the Android TV viewer. The TCL pairing was verified after it rejoined Wi-Fi; keep both Wi-Fi bands on the same LAN and reserve TV and Pi addresses in the router.

**v3.13.0:** Saved Android TVs can be found by subnet checks even when another TV's network announcement is received. Saved LG TVs can now be recovered at a new IP without pairing again when their previously saved MAC address matches. Stale connections at the same IP are refreshed, too. These checks run automatically and through **Find saved TVs**.
