package com.ctrlfreak.magicmirror;

import java.net.HttpURLConnection;
import java.net.URL;

final class MirrorProbe implements Runnable {
    private final MirrorDreamService service;
    private final String url;

    MirrorProbe(MirrorDreamService service, String url) {
        this.service = service;
        this.url = url;
    }

    @Override public void run() {
        boolean online = false;
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setRequestMethod("HEAD");
            connection.setConnectTimeout(3000);
            connection.setReadTimeout(3000);
            online = connection.getResponseCode() < 500;
        } catch (Exception ignored) {
            // A local clock remains visible while the Pi is unreachable.
        } finally {
            if (connection != null) connection.disconnect();
        }
        service.probeOnline = online;
        service.probeReady = true;
    }
}
