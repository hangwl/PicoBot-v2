# Future plans

Ideas that are decided but not scheduled. Nothing here is built; what was
tried and learned lives in [learnings.md](learnings.md).

## On indefinite hold

- **"Go home" rest measure** — leave the map to rest, instead of idling
  in it. The scheduled idle breaks (`break_every_minutes` /
  `break_minutes`, `Machine::break_due`) exist but aren't wanted for real
  use, because a bot standing still in a map looks stuck; leaving the map
  is how a person rests. No design yet. If it is ever picked up, it
  replaces or extends the break code in `core/src/bot/machine.rs`.

## Decided against, for now

- **Match the K75's 8-byte endpoint-3 packet** in the TinyUSB firmware
  (it uses 64): matching costs up to 8 ms per reply line; 64 is the one
  deliberate descriptor difference.
