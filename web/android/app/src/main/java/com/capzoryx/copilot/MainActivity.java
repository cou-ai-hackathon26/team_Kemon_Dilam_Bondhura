package com.capzoryx.copilot;

import android.content.Intent;
import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(CapzoryxPush.class);
        registerPlugin(CapzoryxSms.class);
        super.onCreate(savedInstanceState);
    }

    // The app accepts only plain text shares. Do not pass file streams,
    // clip data, URI grants, or other extras to plugins.
    @Override
    protected void onNewIntent(Intent intent) {
        if (intent != null && (Intent.ACTION_SEND.equals(intent.getAction()) ||
                Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction()))) {
            String text = intent.getStringExtra(Intent.EXTRA_TEXT);
            if (!Intent.ACTION_SEND.equals(intent.getAction()) || !"text/plain".equals(intent.getType())
                    || text == null || text.trim().isEmpty() || text.length() > 2000) {
                return;
            }
            Intent share = new Intent(Intent.ACTION_SEND).setType("text/plain");
            share.putExtra(Intent.EXTRA_TEXT, text);
            super.onNewIntent(share);
            return;
        }
        super.onNewIntent(intent);
    }
}
