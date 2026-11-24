package com.capzoryx.copilot;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.provider.Telephony;
import android.telephony.SmsMessage;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * A new wallet or bank payment SMS: tell the open app so it can add it for review, or, when the app is closed, show a
 * notification that opens it. The message text stays on the phone until the app sends it to the owner's workspace.
 */
public class SmsReceiver extends BroadcastReceiver {
    static final String CHANNEL = "wallet_sms";
    private static final int NOTIFICATION = 7316;

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Telephony.Sms.Intents.SMS_RECEIVED_ACTION.equals(intent.getAction())) return;
        SmsMessage[] parts;
        try {
            parts = Telephony.Sms.Intents.getMessagesFromIntent(intent);
        } catch (RuntimeException error) {
            return;
        }
        if (parts == null) return;
        Map<String, StringBuilder> bodies = new LinkedHashMap<>();
        long date = System.currentTimeMillis();
        for (SmsMessage part : parts) {
            if (part == null || part.getOriginatingAddress() == null) continue;
            String sender = part.getOriginatingAddress();
            StringBuilder body = bodies.get(sender);
            if (body == null) {
                body = new StringBuilder();
                bodies.put(sender, body);
            }
            body.append(part.getMessageBody() == null ? "" : part.getMessageBody());
            date = part.getTimestampMillis();
        }
        boolean wallet = false;
        boolean told = false;
        for (Map.Entry<String, StringBuilder> entry : bodies.entrySet()) {
            String body = entry.getValue().toString();
            if (!CapzoryxSms.fromWallet(entry.getKey(), body) || body.length() > 2000) continue;
            wallet = true;
            told = CapzoryxSms.deliver(entry.getKey(), body, date) || told;
        }
        if (wallet && !told) notifyOwner(context);
    }

    private static void notifyOwner(Context context) {
        NotificationManagerCompat manager = NotificationManagerCompat.from(context);
        if (!manager.areNotificationsEnabled()) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(CHANNEL, "Payment messages", NotificationManager.IMPORTANCE_DEFAULT);
            channel.setDescription("New bKash, Nagad or bank payments to add to your books");
            context.getSystemService(NotificationManager.class).createNotificationChannel(channel);
        }
        Intent open = new Intent(context, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent tap = PendingIntent.getActivity(context, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        NotificationCompat.Builder note = new NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("New payment message")
            .setContentText("Open Capzoryx to review and add it to your books.")
            .setAutoCancel(true)
            .setContentIntent(tap)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT);
        try {
            manager.notify(NOTIFICATION, note.build());
        } catch (SecurityException error) {
            // Notification permission was withdrawn; the app still finds the message next time it opens.
        }
    }
}
