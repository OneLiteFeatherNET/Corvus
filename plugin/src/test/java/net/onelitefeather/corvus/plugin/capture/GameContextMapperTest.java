package net.onelitefeather.corvus.plugin.capture;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Map;
import org.junit.jupiter.api.Test;

class GameContextMapperTest {

    private static GameContextMapper mapper(boolean strip, Map<String, String> servers) {
        return new GameContextMapper(servers, "unknown", strip);
    }

    @Test
    void exactMatchWinsOverStrippedMatch() {
        var m = mapper(true, Map.of("lobby-1", "special", "lobby", "lobby"));
        assertEquals("special", m.contextFor("Lobby-1"));
    }

    @Test
    void instanceSuffixIsStrippedToTheTaskContext() {
        var m = mapper(true, Map.of("lobby", "lobby"));
        assertEquals("lobby", m.contextFor("Lobby-2"));
    }

    @Test
    void hyphenatedTaskNamesKeepTheirInnerHyphens() {
        var m = mapper(true, Map.of("team-build", "build", "voyager-setup", "setup"));
        assertEquals("build", m.contextFor("Team-Build-1"));
        assertEquals("setup", m.contextFor("Voyager-Setup-1"));
    }

    @Test
    void nameWithoutSuffixIsLookedUpUnchanged() {
        var m = mapper(true, Map.of("lobby", "lobby"));
        assertEquals("lobby", m.contextFor("lobby"));
        assertEquals("unknown", m.contextFor("survival"));
    }

    @Test
    void trailingHyphenOrNonDigitSuffixIsNotStripped() {
        var m = mapper(true, Map.of("foo", "foo"));
        assertEquals("unknown", m.contextFor("Foo-"));
        assertEquals("unknown", m.contextFor("Foo-1a"));
    }

    @Test
    void onlyOneSuffixIsStripped() {
        var m = mapper(true, Map.of("a", "a", "a-1", "a1"));
        assertEquals("a1", m.contextFor("A-1-2"));
        assertEquals("unknown", mapper(true, Map.of("a", "a")).contextFor("A-1-2"));
    }

    @Test
    void lookupIsCaseInsensitive() {
        var m = mapper(true, Map.of("elytrarace", "race"));
        assertEquals("race", m.contextFor("ElytraRace-1"));
        assertEquals("race", m.contextFor("ELYTRARACE"));
    }

    @Test
    void disabledOptionTurnsStrippingOff() {
        var m = mapper(false, Map.of("lobby", "lobby"));
        assertEquals("unknown", m.contextFor("Lobby-2"));
        assertEquals("lobby", m.contextFor("Lobby"));
    }

    @Test
    void nullServerNameYieldsDefault() {
        assertEquals("unknown", mapper(true, Map.of()).contextFor(null));
    }
}
