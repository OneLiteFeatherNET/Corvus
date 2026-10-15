package net.onelitefeather.corvus.plugin.capture;

import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicLong;

/** Unbounded-count, byte-bounded, lock-free multi-producer queue with a single consumer. */
public final class ByteBoundedQueue implements CaptureSink {

    private record Entry(WriteItem item, long size) {
    }

    private final ConcurrentLinkedQueue<Entry> queue = new ConcurrentLinkedQueue<>();
    private final AtomicLong bytes = new AtomicLong();
    private final long maxBytes;

    public ByteBoundedQueue(long maxBytes) {
        this.maxBytes = maxBytes;
    }

    @Override
    public boolean offer(WriteItem item) {
        long size = item.sizeBytes();
        if (bytes.addAndGet(size) > maxBytes) {
            bytes.addAndGet(-size);
            return false;
        }
        queue.add(new Entry(item, size));
        return true;
    }

    @Override
    public void offerControl(WriteItem item) {
        long size = item.sizeBytes();
        bytes.addAndGet(size);
        queue.add(new Entry(item, size));
    }

    /** Single consumer only. */
    public WriteItem poll() {
        Entry e = queue.poll();
        if (e == null) {
            return null;
        }
        bytes.addAndGet(-e.size);
        return e.item;
    }

    public long bytes() {
        return bytes.get();
    }

    public long maxBytes() {
        return maxBytes;
    }
}
