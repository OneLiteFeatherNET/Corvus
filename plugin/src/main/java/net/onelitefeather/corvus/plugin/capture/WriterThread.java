package net.onelitefeather.corvus.plugin.capture;

import java.util.concurrent.locks.LockSupport;

/** The single background thread that runs a {@link CaptureWriter}. */
public final class WriterThread {

    private static final long IDLE_PARK_NANOS = 5_000_000L;

    private final CaptureWriter writer;
    private final Thread thread;
    private volatile boolean running = true;

    public WriterThread(CaptureWriter writer) {
        this.writer = writer;
        this.thread = new Thread(this::run, "corvus-writer");
        this.thread.setDaemon(true);
    }

    public void start() {
        thread.start();
    }

    /** Stops the thread after it has drained the queue and closed all files. */
    public void stop() {
        running = false;
        LockSupport.unpark(thread);
        try {
            thread.join(15_000L);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void run() {
        while (running) {
            int processed;
            try {
                processed = writer.drain(1024);
                writer.flushDue();
            } catch (Throwable t) {
                processed = 0;
            }
            if (processed == 0) {
                LockSupport.parkNanos(IDLE_PARK_NANOS);
            }
        }
        writer.closeAll();
    }
}
