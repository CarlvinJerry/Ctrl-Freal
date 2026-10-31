# MagicMirror TV viewer

A small Android TV WebView app that displays the Pi's MagicMirror page in immersive full screen. Package: `com.ctrlfreak.magicmirror`. Ctrl Freak's MagicMirror button checks for the package through ADB, installs `static/magicmirror-viewer.apk` when absent, and starts this activity with the configured `REMOTE_MAGICMIRROR_URL`. Without authorized ADB it falls back to the TV browser. The TV needs network access to the Pi's MagicMirror service.

The viewer APK must be signed before packaging. Build using Android SDK platform 28 or later, Android build tools, and JDK. Do not commit a signing key.
