package com.ctrlfreak.magicmirror;

import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Fit the local TV WebView without changing MagicMirror's server layout. */
public class FitWebViewClient extends WebViewClient {
    @Override public void onPageFinished(WebView view, String url) {
        int width = Math.max(view.getWidth(), view.getResources().getDisplayMetrics().widthPixels);
        int height = Math.max(view.getHeight(), view.getResources().getDisplayMetrics().heightPixels);
        view.evaluateJavascript("(function(){" +
            "if(window.ctrlFreakFit)return;window.ctrlFreakFit=true;" +
            "var m=document.querySelector('meta[name=viewport]');" +
            "if(!m){m=document.createElement('meta');m.name='viewport';document.head.appendChild(m);}" +
            "m.content='width=" + width + ",initial-scale=1,minimum-scale=1,maximum-scale=1,user-scalable=no';" +
            "function fit(){var b=document.body,d=document.documentElement;if(!b)return;" +
            "var bg=document.querySelector('.region.fullscreen.below');" +
            "if(bg){" +
            "bg.style.setProperty('position','fixed','important');" +
            "bg.style.setProperty('inset','0','important');" +
            "bg.style.setProperty('width','100%','important');" +
            "bg.style.setProperty('height','100%','important');" +
            "bg.style.setProperty('z-index','-1','important');" +
            "bg.style.setProperty('visibility','visible','important');}" +
            "var vw=window.innerWidth,vh=window.innerHeight;" +
            "b.style.removeProperty('transform');b.style.removeProperty('transform-origin');" +
            "b.style.setProperty('width',vw+'px','important');" +
            "b.style.setProperty('height',vh+'px','important');" +
            "b.style.setProperty('margin','0','important');" +
            "b.style.setProperty('padding','0','important');" +
            "b.style.setProperty('background','transparent','important');" +
            "b.style.setProperty('z-index','1','important');" +
            "var regions=b.querySelectorAll('.region:not(.fullscreen)');" +
            "for(var i=0;i<regions.length;i++)regions[i].style.setProperty('z-index','2','important');" +
            "var sideScale=Math.min(1," + height + "/1200);" +
            "var left=b.querySelector('.region.top.left');" +
            "var right=b.querySelector('.region.top.right');" +
            "if(left){left.style.setProperty('transform','scale('+sideScale+')','important');left.style.setProperty('transform-origin','top left','important');}" +
            "if(right){right.style.setProperty('transform','scale('+sideScale+')','important');right.style.setProperty('transform-origin','top right','important');}" +
            "b.style.setProperty('overflow','hidden','important');" +
            "d.style.setProperty('overflow','hidden','important');" +
            "}" +
            "setTimeout(fit,100);setInterval(fit,3000);window.addEventListener('resize',fit);" +
            "})()", null);
    }
}
