/**
 * Public API of the Corvus Velocity plugin.
 *
 * <p><strong>Corvus scores are decision aids.</strong> A score is a probability-like model output, not a
 * verdict. Only scores of stage {@link net.onelitefeather.corvus.api.ScoreStage#ENFORCE} are meant to drive
 * automated actions in other plugins; {@link net.onelitefeather.corvus.api.ScoreStage#CANARY} scores are
 * informational. Corvus itself never sanctions players, it only reports scores.
 */
package net.onelitefeather.corvus.api;
