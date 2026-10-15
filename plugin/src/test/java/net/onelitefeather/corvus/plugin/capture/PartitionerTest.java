package net.onelitefeather.corvus.plugin.capture;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PartitionerTest {

    private static byte[] id(int b0, int b1, int b2, int b3) {
        byte[] id = new byte[16];
        id[0] = (byte) b0;
        id[1] = (byte) b1;
        id[2] = (byte) b2;
        id[3] = (byte) b3;
        id[15] = (byte) 0x7f; // bytes beyond the first four must not matter
        return id;
    }

    @Test
    void firstFourBytesBigEndianModSixteen() {
        Partitioner p = new Partitioner(16);

        assertEquals(0, p.partition(id(0, 0, 0, 0)));
        assertEquals(1, p.partition(id(0, 0, 0, 1)));
        assertEquals(15, p.partition(id(0, 0, 0, 0x1f)));
        assertEquals(0x0123_4567 % 16, p.partition(id(0x01, 0x23, 0x45, 0x67)));
    }

    @Test
    void highBitIsTreatedAsUnsigned() {
        // 0xffffffff = 4294967295; mod 16 = 15, mod 10 = 5 (a signed int would give negative values)
        assertEquals(15, new Partitioner(16).partition(id(0xff, 0xff, 0xff, 0xff)));
        assertEquals(5, new Partitioner(10).partition(id(0xff, 0xff, 0xff, 0xff)));
        assertEquals(2868903936L % 7, new Partitioner(7).partition(id(0xab, 0, 0, 0)));
    }

    @Test
    void tokenHasTwoDigitsByDefault() {
        Partitioner p = new Partitioner(16);

        assertEquals("00", p.token(id(0, 0, 0, 0)));
        assertEquals("09", p.token(id(0, 0, 0, 9)));
        assertEquals("15", p.token(id(0, 0, 0, 15)));
    }

    @Test
    void tokenWidthStaysTwoUpToOneHundredPartitions() {
        assertEquals("99", new Partitioner(100).token(id(0, 0, 0, 99)));
    }

    @Test
    void tokenWidthGrowsBeyondOneHundredPartitions() {
        Partitioner p = new Partitioner(128);

        assertEquals("000", p.token(id(0, 0, 0, 0)));
        assertEquals("007", p.token(id(0, 0, 0, 7)));
        assertEquals("127", p.token(id(0, 0, 0, 127)));
    }

    @Test
    void singlePartitionIsAlwaysZeroZero() {
        assertEquals("00", new Partitioner(1).token(id(0xde, 0xad, 0xbe, 0xef)));
    }

    @Test
    void subjectJoinsPrefixProxyAndToken() {
        assertEquals("corvus.capture.v1.proxy-a.03",
                new Partitioner(16).subject("corvus.capture.v1", "proxy-a", id(0, 0, 0, 3)));
    }

    @Test
    void shortIdsAreZeroPaddedOnTheRight() {
        // {0, 1, 0} is read as 0x00010000 = 65536; 65536 mod 7 = 2
        assertEquals(2, new Partitioner(7).partition(new byte[] {0, 1, 0}));
        assertEquals(0, new Partitioner(16).partition(new byte[0]));
    }

    @Test
    void nonPositivePartitionCountIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> new Partitioner(0));
    }
}
