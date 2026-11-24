package com.capzoryx.copilot;

import android.Manifest;
import android.database.Cursor;
import android.net.Uri;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.util.Locale;

/**
 * Reads mobile-wallet and bank payment messages (bKash, Nagad, Rocket, Upay, banks) from the SMS inbox, only after
 * the owner allows it. Other messages are never read past the sender check and never leave the phone.
 */
@CapacitorPlugin(
    name = "CapzoryxSms",
    permissions = { @Permission(alias = "sms", strings = { Manifest.permission.READ_SMS, Manifest.permission.RECEIVE_SMS }) }
)
public class CapzoryxSms extends Plugin {
    private static volatile CapzoryxSms active;
    private static final String[] SENDERS = {
        "bkash", "nagad", "16216", "rocket", "upay", "tcash", "tap", "mcash", "surecash", "okwallet", "cellfin",
        "dbbl", "bracbank", "brac", "citybank", "ebl", "islamibank", "ibbl", "dutchbangla", "sonali", "janata", "agrani",
        "pubali", "ucb", "mtb", "primebank", "bankasia", "standardchartered", "scb", "hsbc", "southeast", "jamuna", "ab bank"
    };

    @Override
    public void load() {
        active = this;
    }

    @Override
    protected void handleOnDestroy() {
        if (active == this) active = null;
    }

    static boolean fromWallet(String sender, String body) {
        if (sender == null || body == null) return false;
        String who = sender.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9 ]", "");
        for (String name : SENDERS) {
            if (who.contains(name)) return true;
        }
        return false;
    }

    /** Called by SmsReceiver: true when the app is open and was told, so no notification is needed. */
    static boolean deliver(String sender, String body, long date) {
        CapzoryxSms plugin = active;
        if (plugin == null || !plugin.hasListeners("walletSms")) return false;
        JSObject message = new JSObject();
        message.put("sender", sender);
        message.put("body", body);
        message.put("date", date);
        plugin.notifyListeners("walletSms", message);
        return true;
    }

    private JSObject state() {
        JSObject value = new JSObject();
        value.put("granted", getPermissionState("sms") == PermissionState.GRANTED);
        return value;
    }

    @PluginMethod
    public void status(PluginCall call) {
        call.resolve(state());
    }

    @PluginMethod
    public void requestAccess(PluginCall call) {
        if (getPermissionState("sms") == PermissionState.GRANTED) {
            call.resolve(state());
            return;
        }
        requestPermissionForAlias("sms", call, "accessAnswered");
    }

    @PermissionCallback
    private void accessAnswered(PluginCall call) {
        call.resolve(state());
    }

    @PluginMethod
    public void read(PluginCall call) {
        if (getPermissionState("sms") != PermissionState.GRANTED) {
            call.reject("SMS access is off.", "SMS_PERMISSION_REQUIRED");
            return;
        }
        long since = call.getLong("since", 0L);
        int limit = Math.max(1, Math.min(call.getInt("limit", 100), 300));
        JSArray messages = new JSArray();
        String[] columns = { "_id", "address", "body", "date" };
        try (Cursor cursor = getContext().getContentResolver().query(
                Uri.parse("content://sms/inbox"), columns, "date > ?", new String[] { String.valueOf(since) }, "date DESC")) {
            while (cursor != null && cursor.moveToNext() && messages.length() < limit) {
                String sender = cursor.getString(1);
                String body = cursor.getString(2);
                if (!fromWallet(sender, body) || body.length() > 2000) continue;
                JSObject message = new JSObject();
                message.put("id", cursor.getString(0));
                message.put("sender", sender);
                message.put("body", body);
                message.put("date", cursor.getLong(3));
                messages.put(message);
            }
        } catch (SecurityException error) {
            call.reject("SMS access is off.", "SMS_PERMISSION_REQUIRED");
            return;
        } catch (RuntimeException error) {
            call.reject("The SMS inbox could not be read.", "SMS_READ_FAILED");
            return;
        }
        JSObject result = new JSObject();
        result.put("messages", messages);
        call.resolve(result);
    }
}
