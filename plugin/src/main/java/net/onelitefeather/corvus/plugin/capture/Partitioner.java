package net.onelitefeather.corvus.plugin.capture;

/**
 * Maps a session to its partition, the last token of the publish subject
 * {@code <prefix>.<proxy_id>.<partition>} (docs/reference/data-lake.md, section 1).
 *
 * <p>partition = first 4 bytes of the session id as big-endian unsigned int, modulo the partition count. The
 * partition is written as decimal with at least two digits ({@code 00}..{@code 15} for the default 16); for more
 * than 100 partitions the width grows to the digits of the highest partition (128 gives {@code 000}..{@code 127}).
 * Scorers must use the same formatting in their filter subjects.
 */
public final class Partitioner {

    public static final int DEFAULT_PARTITIONS = 16;
    private static final int MIN_DIGITS = 2;

    private final int partitions;
    private final String format;

    public Partitioner(int partitions) {
        if (partitions < 1) {
            throw new IllegalArgumentException("partitions must be >= 1, got " + partitions);
        }
        this.partitions = partitions;
        int digits = Math.max(MIN_DIGITS, Integer.toString(partitions - 1).length());
        this.format = "%0" + digits + "d";
    }

    public int partitions() {
        return partitions;
    }

    /** Partition number of {@code sessionId}; ids shorter than 4 bytes are zero-padded on the right. */
    public int partition(byte[] sessionId) {
        long head = 0;
        for (int i = 0; i < 4; i++) {
            head = (head << 8) | (i < sessionId.length ? sessionId[i] & 0xffL : 0L);
        }
        return (int) (head % partitions);
    }

    /** The zero-padded subject token of {@code sessionId}. */
    public String token(byte[] sessionId) {
        return format.formatted(partition(sessionId));
    }

    /** The publish subject {@code <prefix>.<proxy_id>.<partition>} of {@code sessionId}. */
    public String subject(String subjectPrefix, String proxyId, byte[] sessionId) {
        return subjectPrefix + "." + proxyId + "." + token(sessionId);
    }
}
