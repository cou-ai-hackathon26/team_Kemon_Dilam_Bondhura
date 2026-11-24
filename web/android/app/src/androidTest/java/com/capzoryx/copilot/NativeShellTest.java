package com.capzoryx.copilot;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class NativeShellTest {
    @Test
    public void packagedAppStartsWithNativeOriginAndPolicy() throws Exception {
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            AtomicReference<String> result = new AtomicReference<>();
            long stop = android.os.SystemClock.elapsedRealtime() + 30000;
            String inspect = "Boolean(location.origin === 'https://app.capzoryx.local' && " +
                    "window.Capacitor?.getPlatform() === 'android' && " +
                    "document.querySelector('meta[http-equiv=Content-Security-Policy]')?.content.includes('https://capzoryx.vercel.app') && " +
                    "document.querySelector('#root main, #root form'))";
            do {
                CountDownLatch done = new CountDownLatch(1);
                scenario.onActivity(activity -> activity.getBridge().getWebView().evaluateJavascript(inspect, value -> {
                    result.set(value);
                    done.countDown();
                }));
                assertTrue("WebView did not respond", done.await(3, TimeUnit.SECONDS));
                if ("true".equals(result.get())) break;
                Thread.sleep(150);
            } while (android.os.SystemClock.elapsedRealtime() < stop);
            assertEquals("The packaged UI did not open", "true", result.get());
        }
    }
}
