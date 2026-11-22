package org.liveclip;

import android.content.Context;
import android.content.Intent;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import android.media.MediaRecorder;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.os.Handler;
import android.os.Looper;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.Surface;
import android.view.WindowManager;

import java.io.Closeable;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;

/**
 * Screen capture for Android.
 *
 * MediaProjection mirrors the display into a hardware H.264 encoder; the
 * elementary stream is written to a local socket that ffmpeg reads with
 * "-f h264 -i unix://...". Microphone PCM goes to a second socket so the clips
 * have audio. Nothing is buffered in Java beyond the codec's own queues.
 */
public class ScreenCapture {
    private static final String TAG = "LiveClip";
    private static final String MIME_TYPE = MediaFormat.MIMETYPE_VIDEO_AVC;
    private static final int SAMPLE_RATE = 44100;

    private MediaProjection projection;
    private MediaCodec codec;
    private VirtualDisplay display;
    private LocalServerSocket videoServer;
    private LocalServerSocket audioServer;
    private AudioRecord recorder;
    private Thread videoThread;
    private Thread audioThread;
    private volatile boolean running;
    private volatile boolean audioReady;

    public static ScreenCapture start(Context context, int resultCode, Intent data,
                                      String videoPath, String audioPath,
                                      int width, int height, int fps, int bitrate)
            throws Exception {
        MediaProjectionManager manager = (MediaProjectionManager)
                context.getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        MediaProjection projection = manager.getMediaProjection(resultCode, data);
        if (projection == null) {
            throw new IllegalStateException("screen capture permission was not granted");
        }

        int[] size = resolveSize(context, width, height);
        width = size[0];
        height = size[1];

        final ScreenCapture capture = new ScreenCapture();
        capture.projection = projection;
        projection.registerCallback(new MediaProjection.Callback() {
            @Override
            public void onStop() {
                capture.running = false;
            }
        }, new Handler(Looper.getMainLooper()));

        MediaFormat format = MediaFormat.createVideoFormat(MIME_TYPE, width, height);
        format.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        format.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
        format.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
        format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
        format.setInteger("prepend-sps-pps-to-idr-frames", 1);

        capture.codec = MediaCodec.createEncoderByType(MIME_TYPE);
        capture.codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        Surface surface = capture.codec.createInputSurface();
        capture.codec.start();

        capture.videoServer = new LocalServerSocket(videoPath);
        capture.display = projection.createVirtualDisplay(
                "LiveClipCapture", width, height, 320,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                surface, null, null);

        if (audioPath != null) {
            try {
                capture.startAudio(audioPath);
            } catch (Exception error) {
                Log.w(TAG, "audio capture unavailable: " + error);
                capture.stopAudio();
            }
        }

        capture.running = true;
        capture.videoThread = new Thread(new Runnable() {
            @Override
            public void run() {
                capture.pumpVideo();
            }
        }, "liveclip-video");
        capture.videoThread.start();
        return capture;
    }

    /** Encoder size: the caller's request, or the real display when 0 is given. */
    private static int[] resolveSize(Context context, int width, int height) {
        if (width <= 0 || height <= 0) {
            WindowManager manager = (WindowManager) context.getSystemService(Context.WINDOW_SERVICE);
            DisplayMetrics metrics = new DisplayMetrics();
            manager.getDefaultDisplay().getRealMetrics(metrics);
            width = metrics.widthPixels;
            height = metrics.heightPixels;
        }
        width = Math.max(2, width & ~1);
        height = Math.max(2, height & ~1);
        long pixels = (long) width * (long) height;
        long limit = 1920L * 1080L;
        if (pixels > limit) {
            double scale = Math.sqrt((double) limit / (double) pixels);
            width = Math.max(2, ((int) Math.round(width * scale)) & ~1);
            height = Math.max(2, ((int) Math.round(height * scale)) & ~1);
        }
        return new int[]{width, height};
    }

    public boolean hasAudio() {
        return audioReady;
    }

    public void stop() {
        running = false;
        close(videoServer);
        close(audioServer);
        join(videoThread);
        join(audioThread);
        stopAudio();
        try {
            if (display != null) {
                display.release();
            }
        } catch (Exception ignored) {
        }
        try {
            if (codec != null) {
                codec.stop();
                codec.release();
            }
        } catch (Exception ignored) {
        }
        try {
            if (projection != null) {
                projection.stop();
            }
        } catch (Exception ignored) {
        }
        videoServer = null;
        audioServer = null;
    }

    private void startAudio(String path) throws IOException {
        int minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        int bufferSize = Math.max(minBuffer, SAMPLE_RATE / 2);
        recorder = new AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize);
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            throw new IllegalStateException("microphone is not available");
        }
        audioServer = new LocalServerSocket(path);
        recorder.startRecording();
        audioReady = true;
        audioThread = new Thread(new Runnable() {
            @Override
            public void run() {
                pumpAudio();
            }
        }, "liveclip-audio");
        audioThread.start();
    }

    private void stopAudio() {
        audioReady = false;
        try {
            if (recorder != null) {
                recorder.stop();
                recorder.release();
            }
        } catch (Exception ignored) {
        }
        recorder = null;
    }

    private void pumpVideo() {
        LocalSocket client = null;
        try {
            client = videoServer.accept();
            OutputStream out = client.getOutputStream();
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            while (running) {
                int index = codec.dequeueOutputBuffer(info, 10000);
                if (index < 0) {
                    continue;
                }
                ByteBuffer buffer = codec.getOutputBuffer(index);
                if (buffer != null && info.size > 0) {
                    byte[] chunk = new byte[info.size];
                    buffer.position(info.offset);
                    buffer.limit(info.offset + info.size);
                    buffer.get(chunk);
                    out.write(chunk);
                }
                codec.releaseOutputBuffer(index, false);
            }
        } catch (Exception error) {
            Log.w(TAG, "video loop ended: " + error);
        } finally {
            close(client);
        }
    }

    private void pumpAudio() {
        LocalSocket client = null;
        byte[] buffer = new byte[4096];
        try {
            client = audioServer.accept();
            OutputStream out = client.getOutputStream();
            while (running) {
                int read = recorder.read(buffer, 0, buffer.length);
                if (read > 0) {
                    out.write(buffer, 0, read);
                }
            }
        } catch (Exception error) {
            Log.w(TAG, "audio loop ended: " + error);
        } finally {
            close(client);
        }
    }

    private static void close(Closeable target) {
        if (target == null) {
            return;
        }
        try {
            target.close();
        } catch (IOException ignored) {
        }
    }

    private static void join(Thread thread) {
        if (thread == null) {
            return;
        }
        try {
            thread.join(2000);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }
}
