package ml.docilealligator.infinityforreddit.utils;

import android.content.Context;
import android.os.Build;
import android.speech.tts.TextToSpeech;
import android.widget.Toast;
import androidx.annotation.Nullable;
import androidx.preference.PreferenceManager;
import java.util.Locale;
import ml.docilealligator.infinityforreddit.R;

public class TextToSpeechHelper {

    private final Context context;
    @Nullable
    private TextToSpeech tts;
    private boolean initialized;
    @Nullable
    private String pendingText;

    public TextToSpeechHelper(Context context) {
        this.context = context.getApplicationContext();
    }

    private void initIfNeeded() {
        if (tts == null) {
            // Read preferred engine package from preferences (empty = system default)
            String enginePkg = "";
            try {
                enginePkg = PreferenceManager.getDefaultSharedPreferences(context)
                        .getString(SharedPreferencesUtils.SPEECH_TTS_ENGINE, "");
            } catch (Exception ignored) {
            }

            TextToSpeech.OnInitListener listener = status -> {
                if (tts == null) {
                    // shut down before init completed
                    return;
                }
                if (status == TextToSpeech.SUCCESS) {
                    int result = tts.setLanguage(Locale.getDefault());
                    if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                        tts.setLanguage(Locale.US);
                    }
                    initialized = true;
                    if (pendingText != null) {
                        speak(pendingText);
                        pendingText = null;
                    }
                } else {
                    pendingText = null;
                    Toast.makeText(context, R.string.tts_not_available, Toast.LENGTH_SHORT).show();
                }
            };

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP && enginePkg != null && !enginePkg.isEmpty()) {
                tts = new TextToSpeech(context, listener, enginePkg);
            } else {
                tts = new TextToSpeech(context, listener);
            }
        }
    }

    public void speak(String text) {
        if (text == null || text.isEmpty()) {
            return;
        }
        initIfNeeded();
        if (!initialized) {
            pendingText = text;
            return;
        }
        if (tts != null) {
            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "readAloud");
        }
    }

    public void stop() {
        pendingText = null;
        if (tts != null) {
            tts.stop();
        }
    }

    public boolean isSpeaking() {
        return pendingText != null || (tts != null && tts.isSpeaking());
    }

    public void shutdown() {
        pendingText = null;
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
            initialized = false;
        }
    }
}
