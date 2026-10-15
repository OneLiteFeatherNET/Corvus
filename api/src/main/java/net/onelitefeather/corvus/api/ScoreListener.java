package net.onelitefeather.corvus.api;

/**
 * Receives score updates for players that are online on this proxy.
 *
 * <p>Invoked asynchronously on a Corvus worker thread, never on a Netty or Velocity event thread, so it may do
 * light work but should not block for long: a bounded queue sits in front of the workers and updates are dropped
 * when it is full. An exception thrown by one listener does not affect other listeners.
 *
 * <p>Scores are decision aids. Only {@link ScoreStage#ENFORCE} scores are meant for automated actions, and
 * Corvus never sanctions players.
 */
@FunctionalInterface
public interface ScoreListener {

    /** Called when a newer score for (player, game context, domain) arrived. */
    void onScore(PlayerScore score);
}
