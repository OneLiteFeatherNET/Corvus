package net.onelitefeather.corvus.api;

/**
 * Rollout stage of the model that produced a score. Scores of shadow models are never published to the proxy and
 * therefore have no constant here.
 *
 * <p>Corvus scores are decision aids and Corvus never sanctions players. Only {@link #ENFORCE} scores are meant
 * for automated actions in other plugins.
 */
public enum ScoreStage {

    /** Model on trial: informational only, must not drive any automated action. */
    CANARY,

    /** Model approved for automated decisions in other systems. Still a decision aid, not a verdict. */
    ENFORCE;

    /** True when a score of this stage may drive automated actions. */
    public boolean mayDriveAutomatedActions() {
        return this == ENFORCE;
    }
}
