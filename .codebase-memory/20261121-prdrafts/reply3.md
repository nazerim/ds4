`TOKFP` takes bit 5 and leaves the key-kind logic untouched (`ds4_kvstore.c:185-189`) — it classifies on bits 1 and 2 only. Bits 0-3 are unchanged and bit 4 is left free rather than taken next, so the value stays disjoint from other users of the field.

Compatibility note: a pre-PR reader still restores the KV payload from a stamped file but won't replay its tool map — it finds the stamp where it expects the tool-map header, and skipping the tool map doesn't undo the payload restore.
