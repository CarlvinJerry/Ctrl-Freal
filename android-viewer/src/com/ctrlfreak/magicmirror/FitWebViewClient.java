package com.ctrlfreak.magicmirror;

import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Fit only this TV WebView; never change MagicMirror's server CSS. */
public class FitWebViewClient extends WebViewClient {
    @Override public void onPageFinished(WebView view, String url) {
        view.evaluateJavascript("(function(){" +
            "if(window.ctrlFreakFit)return;window.ctrlFreakFit=true;" +
            "function fit(){var b=document.body,d=document.documentElement;if(!b)return;" +
            "var vw=window.innerWidth,vh=window.innerHeight;" +
            "b.style.removeProperty('transform');b.style.removeProperty('transform-origin');" +
            "b.style.setProperty('width',vw+'px','important');" +
            "b.style.setProperty('height',vh+'px','important');" +
            "b.style.setProperty('margin','0','important');" +
            "b.style.setProperty('padding','0','important');" +
            "var bg=document.querySelector('.region.fullscreen.below');" +
            "if(bg)bg.style.setProperty('visibility','hidden','important');" +
            "var w=Math.max(vw,b.scrollWidth,d.scrollWidth),h=Math.max(vh,b.scrollHeight,d.scrollHeight);" +
            "var s=Math.min(1,vw/w,vh/h);" +
            "b.style.setProperty('width',w+'px','important');" +
            "b.style.setProperty('height',h+'px','important');" +
            "b.style.setProperty('transform-origin','top left','important');" +
            "b.style.setProperty('transform','scale('+s+')','important');" +
            "d.style.setProperty('overflow','hidden','important');" +
            "}" +
            "function backdrop(){var img=document.querySelector('.MMM-SimpleBGSlideshow .images img:last-child');" +
            "if(img&&img.src&&document.documentElement.dataset.mirrorImage!==img.src){" +
            "document.documentElement.dataset.mirrorImage=img.src;" +
            "document.documentElement.style.setProperty('background','linear-gradient(rgba(0,0,0,.18),rgba(0,0,0,.18)),url('+img.src+') center center / cover no-repeat fixed','important');}}" +
            "fit();backdrop();setInterval(function(){fit();backdrop();},3000);window.addEventListener('resize',fit);" +
            "})()", null);
    }
}
