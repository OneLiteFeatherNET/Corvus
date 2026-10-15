package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;

/** Unit of work handed from the Netty threads to the single writer thread. */
public sealed interface WriteItem {

    SessionState session();

    /** Approximate memory held by this item, used for the queue's byte bound. */
    long sizeBytes();

    record Start(SessionState session, Envelope envelope) implements WriteItem {
        @Override
        public long sizeBytes() {
            return 128;
        }
    }

    record Segment(SessionState session, Envelope envelope) implements WriteItem {
        @Override
        public long sizeBytes() {
            return 128;
        }
    }

    record FrameItem(SessionState session, int segment, Frame frame) implements WriteItem {
        @Override
        public long sizeBytes() {
            return 64L + frame.getPayload().size();
        }
    }

    record End(SessionState session, long endedAtMillis) implements WriteItem {
        @Override
        public long sizeBytes() {
            return 64;
        }
    }
}
