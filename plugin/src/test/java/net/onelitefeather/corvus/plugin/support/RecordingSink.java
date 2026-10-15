package net.onelitefeather.corvus.plugin.support;

import net.onelitefeather.corvus.plugin.capture.CaptureSink;
import net.onelitefeather.corvus.plugin.capture.WriteItem;

import java.util.ArrayList;
import java.util.List;

/** In-memory sink that records everything offered to it. */
public final class RecordingSink implements CaptureSink {

    public final List<WriteItem> frames = new ArrayList<>();
    public final List<WriteItem> controls = new ArrayList<>();
    public boolean acceptFrames = true;

    @Override
    public boolean offer(WriteItem item) {
        if (!acceptFrames) {
            return false;
        }
        frames.add(item);
        return true;
    }

    @Override
    public void offerControl(WriteItem item) {
        controls.add(item);
    }
}
