#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ANDROID_JAR="${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-28/android.jar}"
D8_JAR="${D8_JAR:-/home/carlvinjerry/tcl-remote/d8.jar}"
SIGNING_KEY="${SIGNING_KEY:-/home/carlvinjerry/tcl-remote/.magicmirror-signing.jks}"
SIGNING_PASS_FILE="${SIGNING_PASS_FILE:-/home/carlvinjerry/tcl-remote/.magicmirror-signing-pass}"
OUT="${OUT:-../static/magicmirror-viewer.apk}"
test -f "$ANDROID_JAR" && test -f "$D8_JAR"
mkdir -p build/gen build/classes build/dex
find build/classes -type f -name '*.class' -delete
aapt package -f -m -J build/gen -M AndroidManifest.xml -S res -I "$ANDROID_JAR"
javac -g:none -source 8 -target 8 -classpath "$ANDROID_JAR" -d build/classes \
  src/com/ctrlfreak/magicmirror/ViewerActivity.java \
  src/com/ctrlfreak/magicmirror/FitWebViewClient.java \
  src/com/ctrlfreak/magicmirror/MirrorDreamService.java \
  build/gen/com/ctrlfreak/magicmirror/R.java
jar cf build/classes.jar -C build/classes .
java -cp "$D8_JAR" com.android.tools.r8.D8 --lib "$ANDROID_JAR" \
  --min-api 23 --output build/dex build/classes.jar
aapt package -f -M AndroidManifest.xml -S res -I "$ANDROID_JAR" -F build/unsigned.apk
(cd build/dex && aapt add ../unsigned.apk classes.dex)
zipalign -f 4 build/unsigned.apk build/aligned.apk
if [[ ! -f "$SIGNING_PASS_FILE" ]]; then
  umask 077
  openssl rand -hex 24 > "$SIGNING_PASS_FILE"
fi
export MAGICMIRROR_SIGN_PASS="$(cat "$SIGNING_PASS_FILE")"
if [[ ! -f "$SIGNING_KEY" ]]; then
  keytool -genkeypair -keystore "$SIGNING_KEY" -alias magicmirror \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Ctrl Freak MagicMirror" \
    -storepass "$MAGICMIRROR_SIGN_PASS" -keypass "$MAGICMIRROR_SIGN_PASS"
fi
apksigner sign --ks "$SIGNING_KEY" --ks-key-alias magicmirror \
  --ks-pass env:MAGICMIRROR_SIGN_PASS --key-pass env:MAGICMIRROR_SIGN_PASS \
  --out "$OUT" build/aligned.apk
apksigner verify "$OUT"
echo "Built $OUT"
