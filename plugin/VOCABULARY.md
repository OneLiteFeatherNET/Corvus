# Corvus packet vocabulary

`CorvusPacket` maps PacketEvents `PacketType` constants to a stable `uint32` that is written to
`Frame.packet_type`. It is the version-independent key the analyzers and models use; the wire packet id
changes between Minecraft versions, this id does not.

Rules:

- Ids are **append-only**. Never renumber, never reuse an id, never delete an entry. If PacketEvents drops a
  packet type, keep the id reserved and remove only the mapping in code (leave a comment).
- `0` means "unmapped" (the frame is still recorded, with its wire id and raw payload).
- Ranges: `1-99` serverbound play, `100-199` clientbound play, `200-299` configuration. New entries take the
  next free number in their range.
- Chat, commands, signing and player-list packets have no id on purpose: they are never recorded
  (see `PrivacyFilter`).
