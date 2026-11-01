# Ctrl Freak

Self-hosted remote for Android TV / Google TV and LG webOS. The Raspberry Pi runs the server; the browser is the remote.

## Version

Current app version: **3.5.0**. `version.py` is the single source for the displayed version and bootstrap API. Use semantic versioning (`MAJOR.MINOR.PATCH`): increment major for incompatible releases, minor for backward-compatible features, and patch for fixes. Update `version.py` with each release.

## Add a TV

Open **Add TV** beside the TV selector. **Scan for TVs** searches from the Pi for Android TV Remote v2 and LG webOS devices. If discovery misses a TV, choose its type and enter its private IPv4 address manually. Turn the TV on before pairing.

- **Android TV / Google TV:** The TV must have Android TV Remote Service. Start pairing, enter the code shown on the TV, then finish. ADB is only needed for optional diagnostics and installed-app discovery.
- **LG webOS:** Enable the TV's mobile-app connection setting (the wording varies by model). Start pairing and accept the request on the TV using its physical remote. No numeric code is used. WebOS power-on from standby may need Wake-on-LAN support and is not provided yet.

For the **LJ570U (webOS 3.5)**, look under **Settings → All Settings → Network → LG Connect Apps** before pairing. The pairing popup repeats these steps. LG lists this model family with a built-in web browser and mobile-app support; actual compatibility will be verified on the TV during deployment.

The Pi needs `Flask`, `waitress`, `androidtvremote2`, `zeroconf`, and `aiowebostv` from `requirements.txt`. These are installed only during an approved deployment. Do not commit `tvs.json`, PIN/session files, pairing certificates, or webOS client keys. Newly paired TVs appear immediately in the selector; existing TV settings and credentials remain in place.

Discovery requires the Pi and TVs to share a network that permits mDNS and SSDP multicast. Manual IP entry remains available when discovery is blocked. Some remote buttons depend on the selected TV's protocol and firmware.

## MagicMirror

**MagicMirror** appears in Apps and can be added to Favorites. On Android TV, tapping it checks for the bundled `com.ctrlfreak.magicmirror` viewer, installs the signed APK through authorized ADB when needed, and opens the live page in immersive full screen. If ADB is unavailable, it opens the TV browser. LG webOS also uses its browser. The default address is `http://192.168.1.21:8080/`; set `REMOTE_MAGICMIRROR_URL` for a different reachable address. MagicMirror must listen on the Pi's LAN interface and allow the TV's IP. On older webOS 3.5 browsers, complex MagicMirror modules may need a simpler layout. Ctrl Freak does not restart or reconfigure the MagicMirror service. The viewer source and reproducible build script are in `android-viewer/`; preserve the Pi's private viewer signing key for future APK upgrades.

## Live TV information

Status refreshes every three seconds. On Android TVs that do not publish volume or foreground app through Android TV Remote v2, Ctrl Freak reads those values through ADB about every ten seconds. Device diagnostics and app discovery reconnect ADB when the TV is reachable and previously authorized. A newly opened phone selects a reachable TV when an older saved TV address is offline. Enable network ADB on the TV for these features and for automatic MagicMirror viewer installation.

Paired TVs and credentials stay in `tvs.json` on the Pi. Every two minutes, Ctrl Freak searches for disconnected saved Android TVs and verifies a discovered address with the TV's existing pairing certificate before updating the saved IP. Discovery tries network announcements first, then checks the saved TV's private /24 subnet for Android TV Remote or ADB ports. The **Find saved TVs** button in Settings runs the same check immediately. **Refresh memory & storage** reads the selected TV's current RAM and storage values; those values also refresh every 30 seconds. The TV must be awake and accept its existing pairing certificate before its IP can be changed automatically. LG webOS pairing stays saved, but IP recovery for LG still requires pairing again if its IP changes.

## Household features

**Guest remote:** The owner creates a temporary six-digit PIN in Settings. It grants only volume, playback, and navigation controls. The PIN expires after the chosen duration (1–72 hours) or can be revoked immediately. Only a salted hash is saved in `.guest_access.json`; the PIN is shown once. Pairing, preferences, diagnostics, scenes, and other Settings endpoints remain owner-only.

**Shared preferences:** Theme, accent, haptics, favorites, and scenes are saved in `preferences.json` on the Pi and update across signed-in phones. The first phone to open the new version imports its existing browser preferences if the Pi has no preferences file. The selected TV remains local to each phone.

**Room scenes:** Create a scene for the selected TV in Settings with an app and/or target volume. Tap it on the Remote tab to run it. Android TV volume uses ADB's media-session command; LG webOS uses its own volume API. The scene checks the app is installed before launching it. Network ADB must be authorized for Android TV volume and app discovery.

**MagicMirror screensaver:** On Android TV, Settings can install the bundled viewer as a system screensaver and restore the previous choice. The viewer fits the 1920×1080 mirror content within the TV while its slideshow background fills the screen edge to edge. If the Pi is offline, the viewer shows a local full-screen clock and checks every 30 seconds until MagicMirror returns. Android does not allow this unprivileged viewer to switch to the TV's original system screensaver while the Pi is off. This setting uses authorized ADB and is unavailable on LG webOS. The TV's firmware controls when idle screensavers start; Ctrl Freak does not change the idle timeout.

**Jack Sparrow:** Its own tab searches recent Internet Archive catalog items and torrent RSS feeds supplied by the owner. Results appear in a scrollable grid with type filters, ordering, language labels, and a downloaded marker. Explicitly English-labelled results are shown first; when torrent files include English-labelled variants, those are preferred for download and import. Unlabelled sources cannot guarantee language. It tracks title names, checks for matching releases every ten minutes, keeps an inbox across phones, and asks the owner to review each download. The owner selects a preferred quality and Plex library before submitting a torrent to the local Transmission daemon. Completed media are linked or copied from a staging directory into Movies, Series, Music, or PersonalVids under `JACK_MEDIA_ROOT` (default `/media/carlvinjerry/Views`). Plex's library scanning must include those paths. On the current Pi only Movies and Series are configured as Plex libraries; Music and Other Videos require a library to be added in Plex. The UI shows progress and completion. The torrent RPC endpoint is loopback-only and not exposed directly to phones. Archive rights and third-party feed items should be checked before downloading; the app does not validate distribution rights. Torrent searches cover configured feeds and the Archive catalog, not every release on the internet.
