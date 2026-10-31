package com.ctrlfreak.magicmirror;

import android.webkit.WebView;
import android.webkit.WebViewClient;

public class FitWebViewClient extends WebViewClient {
    @Override public void onPageFinished(WebView view, String url) {
        view.evaluateJavascript("(function(){" +
            "function fit(){var w=1920,h=1080,vw=window.innerWidth,vh=window.innerHeight;" +
            "var s=Math.min(vw/w,vh/h)*0.94,x=(vw-w*s)/2,y=(vh-h*s)/2;" +
            "var b=document.body,d=document.documentElement;" +
            "d.style.setProperty('overflow','hidden','important');" +
            "b.style.setProperty('width',w+'px','important');" +
            "b.style.setProperty('height',h+'px','important');" +
            "b.style.setProperty('margin','0','important');" +
            "b.style.setProperty('padding','0','important');" +
            "b.style.setProperty('background','transparent','important');" +
            "b.style.setProperty('transform-origin','top left','important');" +
            "b.style.setProperty('transform','translate('+x+'px,'+y+'px) scale('+s+')','important');" +
            "var bg=document.querySelector('.region.fullscreen.below');" +
            "if(bg)bg.style.setProperty('visibility','hidden','important');" +
            "}" +
            "function backdrop(){var img=document.querySelector('.MMM-SimpleBGSlideshow .images img:last-child');" +
            "if(img&&img.src&&document.documentElement.dataset.mirrorImage!==img.src){" +
            "document.documentElement.dataset.mirrorImage=img.src;" +
            "document.documentElement.style.setProperty('background','linear-gradient(rgba(0,0,0,.18),rgba(0,0,0,.18)),url('+img.src+') center center / cover no-repeat fixed','important');}}" +
            "fit();backdrop();setInterval(backdrop,1000);window.addEventListener('resize',fit);" +
            "})()", null);
    }
}
