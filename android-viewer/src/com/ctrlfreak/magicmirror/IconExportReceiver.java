package com.ctrlfreak.magicmirror;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import java.io.File;
import java.io.FileOutputStream;

/** Exports the TV's own installed app icon for the paired Pi to cache. */
public final class IconExportReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String packageName = intent.getStringExtra("package");
        if (packageName == null || !packageName.matches("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+")) return;
        try {
            File directory = context.getExternalFilesDir("icons");
            if (directory == null || (!directory.exists() && !directory.mkdirs())) return;
            save(context.getPackageManager().getApplicationIcon(packageName),
                 new File(directory, packageName + ".png"), 128, 128);
            Drawable banner = context.getPackageManager().getApplicationBanner(packageName);
            if (banner != null) save(banner, new File(directory, packageName + ".banner.png"), 320, 180);
        } catch (Exception ignored) {
            // Some TV packages do not expose an icon. The remote keeps its text fallback.
        }
    }

    private static void save(Drawable drawable, File target, int width, int height) throws Exception {
        Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        drawable.setBounds(0, 0, width, height);
        drawable.draw(canvas);
        try (FileOutputStream output = new FileOutputStream(target)) {
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, output);
        }
        bitmap.recycle();
    }
}
