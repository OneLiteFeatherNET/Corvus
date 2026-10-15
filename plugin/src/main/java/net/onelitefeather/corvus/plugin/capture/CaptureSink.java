package net.onelitefeather.corvus.plugin.capture;

/** Port between the capture engine (Netty threads) and the writer. Must never block. */
public interface CaptureSink {

    /** Offers a frame; false means the bound was hit and the frame must be dropped. */
    boolean offer(WriteItem item);

    /** Offers a small lifecycle item (start/segment/end); it is accepted even if the queue is full. */
    void offerControl(WriteItem item);
}
