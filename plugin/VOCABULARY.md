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
- Privacy-dropped types (chat, commands, signing, player lists; see `PrivacyFilter`) also have ids. The frames
  are never recorded; the ids exist so the analyzer can deny them. Every type in `PrivacyFilter`, in the
  payload-stripped set and in the clientbound payload allowlist (see `PayloadPolicy`) must have an id; a unit
  test enforces this.
- Packet types that are not in the vocabulary are recorded with id `0`, and their payload is only kept in
  `capture.raw_payloads = "all"` (fail closed).
