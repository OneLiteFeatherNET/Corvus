package net.onelitefeather.corvus.plugin.capture;

import io.netty.buffer.ByteBuf;

/** Copies buffer content with absolute-index reads only; reader/writer index and refCnt are never touched. */
public final class PayloadCopier {

    private PayloadCopier() {
    }

    /** Copies the readable bytes (reader index to writer index), truncated to {@code maxBytes}. */
    public static byte[] copyReadable(ByteBuf buf, int maxBytes) {
        int length = Math.min(buf.readableBytes(), maxBytes);
        byte[] out = new byte[length];
        buf.getBytes(buf.readerIndex(), out);
        return out;
    }
}
