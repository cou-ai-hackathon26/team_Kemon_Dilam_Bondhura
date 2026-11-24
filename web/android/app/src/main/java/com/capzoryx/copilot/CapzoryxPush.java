package com.capzoryx.copilot;

import android.app.NotificationManager;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;

@CapacitorPlugin(name = "CapzoryxPush")
public class CapzoryxPush extends Plugin {
    @PluginMethod
    public void configuration(PluginCall call) {
        JSObject value = new JSObject();
        value.put("available", !FirebaseApp.getApps(getContext()).isEmpty());
        call.resolve(value);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        if (FirebaseApp.getApps(getContext()).isEmpty()) {
            call.resolve();
            return;
        }
        FirebaseMessaging messaging = FirebaseMessaging.getInstance();
        messaging.setAutoInitEnabled(false);
        getContext().getSystemService(NotificationManager.class).cancelAll();
        messaging.deleteToken().addOnCompleteListener(task -> {
            if (task.isSuccessful()) call.resolve();
            else call.reject("Could not remove the device notification token.");
        });
    }
}
