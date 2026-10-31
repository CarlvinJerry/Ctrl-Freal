package com.ctrlfreak.magicmirror;

import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Handler;
import android.os.Looper;
import android.service.dreams.DreamService;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class MirrorDreamService extends DreamService implements Runnable {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView webView;
    private TextView fallback;
    private String url;
    private boolean reachable;
    private boolean stopped;
    private boolean probing;
    private long nextCheck;
    volatile boolean probeReady;
    volatile boolean probeOnline;

    @Override public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setFullscreen(true);
        setScreenBright(true);
        setInteractive(false);
        stopped = false;
        reachable = false;
        url = getSharedPreferences("viewer", MODE_PRIVATE).getString("url", "http://192.168.1.21:8080/");
        FrameLayout frame = new FrameLayout(this);
        frame.setBackgroundColor(Color.BLACK);
        webView = new WebView(this);
        webView.setBackgroundColor(Color.BLACK);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        webView.setWebViewClient(new FitWebViewClient());
        webView.setWebChromeClient(new WebChromeClient());
        frame.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        fallback = new TextView(this);
        fallback.setGravity(Gravity.CENTER);
        fallback.setTextColor(Color.WHITE);
        fallback.setTextSize(42);
        fallback.setTypeface(Typeface.create("sans-serif-light", Typeface.NORMAL));
        fallback.setBackgroundColor(Color.BLACK);
        frame.addView(fallback, new FrameLayout.LayoutParams(-1, -1));
        setContentView(frame);
        handler.post(this);
    }

    @Override public void run() {
        if (stopped || fallback == null) return;
        String clock = new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date());
        String date = new SimpleDateFormat("EEEE, MMMM d", Locale.getDefault()).format(new Date());
        fallback.setText(clock + "\n" + date);
        if (probeReady) {
            probeReady = false;
            probing = false;
            if (probeOnline && !reachable) webView.loadUrl(url);
            reachable = probeOnline;
            fallback.setVisibility(reachable ? View.GONE : View.VISIBLE);
        }
        if (!probing && System.currentTimeMillis() >= nextCheck) {
            probing = true;
            nextCheck = System.currentTimeMillis() + 30000;
            new Thread(new MirrorProbe(this, url), "mirror-connection-check").start();
        }
        handler.postDelayed(this, 1000);
    }

    @Override public void onDetachedFromWindow() {
        stopped = true;
        handler.removeCallbacks(this);
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        fallback = null;
        super.onDetachedFromWindow();
    }
}
