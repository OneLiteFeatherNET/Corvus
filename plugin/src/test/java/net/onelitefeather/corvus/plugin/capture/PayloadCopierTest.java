package net.onelitefeather.corvus.plugin.capture;

import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

class PayloadCopierTest {

    private ByteBuf bufferWithPacketIdConsumed() {
        ByteBuf buf = Unpooled.buffer();
        buf.writeByte(0x1A); // packet id, already consumed by PacketEvents
        buf.writeBytes(new byte[]{10, 20, 30, 40});
        buf.readerIndex(1);
        return buf;
    }

    @Test
    void readerAndWriterIndexAreUnchangedAfterCopy() {
        ByteBuf buf = bufferWithPacketIdConsumed();
        int readerBefore = buf.readerIndex();
        int writerBefore = buf.writerIndex();

        PayloadCopier.copyReadable(buf, 1024);

        assertEquals(readerBefore, buf.readerIndex(), "reader index must not move");
        assertEquals(writerBefore, buf.writerIndex(), "writer index must not move");
        buf.release();
    }

    @Test
    void referenceCountIsUnchangedAfterCopy() {
        ByteBuf buf = bufferWithPacketIdConsumed();

        PayloadCopier.copyReadable(buf, 1024);

        assertEquals(1, buf.refCnt(), "ownership stays with the caller");
        buf.release();
    }

    @Test
    void copyContainsOnlyBytesAfterThePacketId() {
        ByteBuf buf = bufferWithPacketIdConsumed();

        byte[] copy = PayloadCopier.copyReadable(buf, 1024);

        assertArrayEquals(new byte[]{10, 20, 30, 40}, copy);
        buf.release();
    }

    @Test
    void copyIsTruncatedToMaximumAndOriginalIsStillIntact() {
        ByteBuf buf = bufferWithPacketIdConsumed();

        byte[] copy = PayloadCopier.copyReadable(buf, 2);

        assertArrayEquals(new byte[]{10, 20}, copy);
        assertEquals(4, buf.readableBytes());
        buf.release();
    }

    @Test
    void mutatingTheCopyDoesNotChangeTheBuffer() {
        ByteBuf buf = bufferWithPacketIdConsumed();

        PayloadCopier.copyReadable(buf, 1024)[0] = 99;

        assertEquals(10, buf.getByte(buf.readerIndex()));
        buf.release();
    }
}
