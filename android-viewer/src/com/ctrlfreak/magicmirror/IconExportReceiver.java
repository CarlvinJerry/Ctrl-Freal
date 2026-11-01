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
            Drawable icon = context.getPackageManager().getApplicationIcon(packageName);
            Bitmap bitmap = Bitmap.createBitmap(128, 128, Bitmap.Config.ARGB_8888);
            Canvas canvas = new Canvas(bitmap);
            icon.setBounds(0, 0, 128, 128);
            icon.draw(canvas);
            File directory = context.getExternalFilesDir("icons");
            if (directory == null || (!directory.exists() && !directory.mkdirs())) return;
            File target = new File(directory, packageName + ".png");
            try (FileOutputStream output = new FileOutputStream(target)) {
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, output);
            }
            bitmap.recycle();
        } catch (Exception ignored) {
            // Some TV packages do not expose an icon. The remote keeps its text fallback.
        }
    }
}
