package press.aresan02.inkferry;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.Proxy;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicReference;

public final class MainActivity extends Activity {
    private static final int OPEN_TXT = 1;
    private static final int SAVE_BOOK = 2;
    // Leave room for the document provider to append a duplicate-name suffix.
    private static final int MAX_SUGGESTED_FILENAME_BYTES = 240;

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final AtomicReference<Export> export = new AtomicReference<>();
    private final ConnectivityManager.NetworkCallback networkCallback =
            new ConnectivityManager.NetworkCallback() {
                @Override public void onLinkPropertiesChanged(Network network, LinkProperties properties) {
                    refreshNetwork();
                }
                @Override public void onLost(Network network) {
                    refreshNetwork();
                }
            };
    private ConnectivityManager connectivity;
    private boolean networkCallbackRegistered;
    private FrameLayout frame;
    private TextView status;
    private WebView webView;
    private ValueCallback<Uri[]> upload;
    private volatile boolean closed;
    private String origin;
    private String adminKey;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        frame = new FrameLayout(this);
        frame.setBackgroundColor(0xff171615);
        frame.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        status = new TextView(this);
        status.setGravity(Gravity.CENTER);
        status.setTextColor(0xfff3ead7);
        status.setTextSize(18);
        status.setText(R.string.starting);
        frame.addView(status, new FrameLayout.LayoutParams(-1, -1));
        setContentView(frame);
        connectivity = getSystemService(ConnectivityManager.class);
        if (connectivity != null) {
            var local = new NetworkRequest.Builder()
                    .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                    .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET)
                    .build();
            connectivity.registerNetworkCallback(local, networkCallback);
            networkCallbackRegistered = true;
        }
    }

    @Override protected void onResume() {
        super.onResume();
        startHost();
    }

    private void startHost() {
        worker.execute(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                String url = Python.getInstance().getModule("android_host")
                        .callAttr("start", getFilesDir().getAbsolutePath(), wifiAddresses())
                        .toString();
                runOnUiThread(() -> showSender(url));
            } catch (RuntimeException error) {
                android.util.Log.e("InkFerry", "Embedded server failed to start", error);
                runOnUiThread(() -> {
                    if (closed || webView != null) return;
                    status.setText(R.string.start_failed);
                    status.setOnClickListener(view -> {
                        status.setText(R.string.starting);
                        startHost();
                    });
                });
            }
        });
    }

    private void refreshNetwork() {
        runOnUiThread(() -> {
            if (!closed) startHost();
        });
    }

    private String wifiAddresses() {
        var addresses = new LinkedHashSet<String>();
        if (connectivity != null) {
            for (Network network : connectivity.getAllNetworks()) {
                NetworkCapabilities caps = connectivity.getNetworkCapabilities(network);
                if (caps == null || !(caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                        || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))) continue;
                LinkProperties links = connectivity.getLinkProperties(network);
                if (links == null) continue;
                for (LinkAddress link : links.getLinkAddresses()) {
                    if (link.getAddress() instanceof Inet4Address) {
                        addresses.add(link.getAddress().getHostAddress());
                    }
                }
            }
        }
        return android.text.TextUtils.join(",", addresses);
    }

    private void showSender(String url) {
        if (closed) return;
        if (webView != null) {
            webView.evaluateJavascript(
                    "window.inkFerryAndroidNetworkChanged && window.inkFerryAndroidNetworkChanged()", null);
            return;
        }
        Uri address = Uri.parse(url);
        origin = "http://127.0.0.1:" + address.getPort();
        adminKey = address.getFragment().substring("key=".length());
        webView = new WebView(this);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setAllowFileAccess(false);
        webView.getSettings().setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        WebView.setWebContentsDebuggingEnabled(false);
        webView.addJavascriptInterface(new Bridge(), "InkFerryAndroid");
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !sameOrigin(request.getUrl());
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return !sameOrigin(Uri.parse(url));
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                                       FileChooserParams params) {
                if (upload != null) upload.onReceiveValue(null);
                upload = callback;
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*")
                        .addCategory(Intent.CATEGORY_OPENABLE);
                try {
                    startActivityForResult(pick, OPEN_TXT);
                } catch (ActivityNotFoundException error) {
                    upload = null;
                    callback.onReceiveValue(null);
                }
                return true;
            }
        });
        frame.removeAllViews();
        frame.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        webView.loadUrl(url);
    }

    private boolean sameOrigin(Uri candidate) {
        return "http".equals(candidate.getScheme()) && "127.0.0.1".equals(candidate.getHost())
                && origin.equals("http://127.0.0.1:" + candidate.getPort());
    }

    private final class Bridge {
        @JavascriptInterface public boolean saveBook(String id, String filename) {
            if (closed || id == null || !id.matches("[a-zA-Z0-9_-]{22}") || filename == null
                    || filename.codePointCount(0, filename.length()) > 120
                    || !filename.matches("[^/\\\\\\p{Cntrl}]+\\.(mobi|epub)")) {
                return false;
            }
            var job = new Export(id, fitDocumentName(filename));
            if (!export.compareAndSet(null, job)) return false;
            runOnUiThread(() -> {
                if (closed) return;
                String mime = filename.endsWith(".epub")
                        ? "application/epub+zip" : "application/x-mobipocket-ebook";
                Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT).setType(mime)
                        .addCategory(Intent.CATEGORY_OPENABLE)
                        .putExtra(Intent.EXTRA_TITLE, job.filename);
                try {
                    startActivityForResult(save, SAVE_BOOK);
                } catch (ActivityNotFoundException error) {
                    finishExport("error", getString(R.string.save_failed));
                }
            });
            return true;
        }
    }

    private static String fitDocumentName(String filename) {
        int end = filename.lastIndexOf('.');
        String suffix = filename.substring(end);
        String fitted = filename;
        while (fitted.getBytes(StandardCharsets.UTF_8).length > MAX_SUGGESTED_FILENAME_BYTES) {
            end = filename.offsetByCodePoints(end, -1);
            fitted = filename.substring(0, end) + suffix;
        }
        return fitted;
    }

    @Override protected void onActivityResult(int requestCode, int result, Intent data) {
        super.onActivityResult(requestCode, result, data);
        Uri document = result == RESULT_OK && data != null ? data.getData() : null;
        if (requestCode == OPEN_TXT && upload != null) {
            ValueCallback<Uri[]> callback = upload;
            upload = null;
            callback.onReceiveValue(document == null ? null : new Uri[]{document});
        } else if (requestCode == SAVE_BOOK) {
            Export job = export.get();
            if (job == null) return;
            if (document == null) finishExport("cancelled", getString(R.string.save_cancelled));
            else worker.execute(() -> writeBook(job, document));
        }
    }

    private void writeBook(Export job, Uri document) {
        String outcome = "saved";
        String message = getString(R.string.saved, job.filename);
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(origin + "/api/books/" + job.id)
                    .openConnection(Proxy.NO_PROXY);
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(15000);
            connection.setRequestProperty("X-Admin-Key", adminKey);
            try (InputStream input = connection.getInputStream();
                 OutputStream output = getContentResolver().openOutputStream(document, "wt")) {
                if (output == null) throw new IOException("Document is not writable");
                byte[] buffer = new byte[64 * 1024];
                for (int count; (count = input.read(buffer)) != -1; ) {
                    output.write(buffer, 0, count);
                }
            }
        } catch (IOException | SecurityException error) {
            outcome = "error";
            message = getString(R.string.save_failed);
            android.util.Log.w("InkFerry", "Could not export a book", error);
        } finally {
            if (connection != null) connection.disconnect();
        }
        String status = outcome;
        String details = message;
        runOnUiThread(() -> finishExport(status, details));
    }

    private void finishExport(String status, String message) {
        export.set(null);
        if (webView != null && !closed) {
            webView.evaluateJavascript("window.inkFerryExportResult && window.inkFerryExportResult("
                    + JSONObject.quote(status) + "," + JSONObject.quote(message) + ")", null);
        }
    }

    @Override protected void onDestroy() {
        closed = true;
        if (networkCallbackRegistered) {
            connectivity.unregisterNetworkCallback(networkCallback);
        }
        if (upload != null) {
            upload.onReceiveValue(null);
            upload = null;
        }
        if (webView != null) {
            webView.removeJavascriptInterface("InkFerryAndroid");
            frame.removeView(webView);
            webView.destroy();
            webView = null;
        }
        worker.execute(() -> {
            if (Python.isStarted()) Python.getInstance().getModule("android_host").callAttr("stop");
        });
        worker.shutdown();
        super.onDestroy();
    }

    private static final class Export {
        final String id;
        final String filename;
        Export(String id, String filename) {
            this.id = id;
            this.filename = filename;
        }
    }
}
