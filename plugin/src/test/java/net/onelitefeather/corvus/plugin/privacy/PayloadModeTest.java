package net.onelitefeather.corvus.plugin.privacy;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PayloadModeTest {

    @Test
    void nullYieldsServerboundDefault() {
        assertEquals(PayloadMode.SERVERBOUND, PayloadMode.parse(null));
    }

    @Test
    void stringsAreCaseInsensitive() {
        assertEquals(PayloadMode.ALL, PayloadMode.parse("ALL"));
        assertEquals(PayloadMode.NONE, PayloadMode.parse(" None "));
    }

    @Test
    void trueIsServerbound() {
        assertEquals(PayloadMode.SERVERBOUND, PayloadMode.parse(Boolean.TRUE));
    }

    @Test
    void falseIsNone() {
        assertEquals(PayloadMode.NONE, PayloadMode.parse(Boolean.FALSE));
    }

    @Test
    void unknownValueIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> PayloadMode.parse("clientbound"));
    }
}
