package com.ctrlfreak.magicmirror;

import android.graphics.Color;
import android.service.dreams.DreamService;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;

public class MirrorDreamService extends DreamService {
    private WebView webView;

    @Override public void onAttachedToWindow() {
        super.onAttachedToWindow();
        setFullscreen(true);
        setScreenBright(true);
        setInteractive(false);
        webView = new WebView(this);
        webView.setBackgroundColor(Color.BLACK);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        webView.setWebViewClient(new FitWebViewClient());
        webView.setWebChromeClient(new WebChromeClient());
        setContentView(webView);
        String url = getSharedPreferences("viewer", MODE_PRIVATE).getString("url", "http://192.168.1.21:8080/");
        webView.loadUrl(url);
    }

    @Override public void onDetachedFromWindow() {
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDetachedFromWindow();
    }
}
