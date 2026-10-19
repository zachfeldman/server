# HA Lights Sync Plugin

Syncs any Home Assistant light to music in real time. Each configured
group of light entities appears as a virtual player; join it to a playing
group the same way you'd join a Hue Lights Sync player, and the lights
react to whatever that group plays.

## Architecture

```
Sendspin Server (PushStream -> group visualizer role -> spectrum + beats)
       |
       v (in-process bridge visualizer role subscribes to the group's role)
Bridge visualizer role (features keyed to playback timestamp)
       |
       v (frame / beats callbacks)
HALightsAudioAnalyzer (bass/treble/overall EMA, beat flash scheduling)   <- this provider
       |
       v (8 Hz render loop -> LightCommand: hue/saturation/brightness/transition)
HALightGroupBridge -> hass provider's HomeAssistantClient -> light.turn_on
       |
       v (Home Assistant's own websocket API, then whatever integration owns the entity)
Home Assistant -> the physical light (Hue, Zigbee, LIFX, Wi-Fi strips, whatever)
```

The bridge registers with the local Sendspin server as an **in-process
external visualizer client** (`register_external_player`), the same
pattern `hue_entertainment` uses - no WebSocket involved for the
in-process half. What's different from a device-specific bridge like
`hue_entertainment`'s DTLS streamer: there is no dedicated low-latency
transport at all. A `LightCommand` is sent as a plain `light.turn_on`
service call through the "Home Assistant" plugin's existing connection
(`mass.get_provider("hass").hass`), which is what lets this plugin work
with *any* light Home Assistant controls rather than needing its own
per-brand transport. The render loop runs at 8 Hz rather than Hue's 30 Hz
because a service call is one request/response round trip through HA, not
a continuous stream; each command carries a `transition` spanning the
render period so the light eases between points instead of visibly
stepping. Each light entity is paced to its own measured round-trip time
rather than the shared render rate, so a slow light sharing a group with a
fast one settles into whatever cadence it can sustain instead of either
starving the fast one or getting hammered with commands it can't keep up
with (see `bridge.py`'s `_dispatch_send`).

## Modes

| Mode | Description |
|------|-------------|
| **Smooth** (default) | Gentle, beat-tinted color drift with bass-driven brightness swell. |
| **Ambient** | Slower drift, bass-reactive saturation. |
| **Flashing** | Strong pulse on every beat, resting brightness stays low between beats. |
| **Pulse** | Brightness continuously tracks the track's overall loudness (full spectrum, not just bass); no beat flash. |

`Energetic`, `Auto`, and `Strobe` existed through 2026-10-19 but were cut
after a real-hardware test found the render loop dying silently (no
exception, no log activity at all) for 10-20+ second stretches on all
three - see "Incubation history" below. The debug-level heartbeat logging
added to `bridge.py`'s render loop (`_render_tick`/`_start_render_loop`/
`_cancel_render_loop`) is still in place, so re-adding any of them has a
real shot at localizing the actual bug on a future real-hardware run.

A **Lock color** setting overrides every mode's hue output with one fixed
value (brightness/saturation keep reacting normally), for staying on a
specific color instead of roaming the color wheel. **Restore lights when
music stops** (on by default) puts each light back to exactly its
pre-sync on/off/brightness/color state once a stream genuinely ends,
instead of leaving it frozen at the last rendered frame. A short white
flash at full brightness confirms every settings change, independent of
whether anything is currently playing.

## Known hardware/protocol limitations

- Some light hardware/integrations ignore `light.turn_on`'s `transition`
  parameter entirely regardless of what's sent (confirmed on TP-Link Kasa
  KL-series strips - see
  [python-kasa#389](https://github.com/python-kasa/python-kasa/issues/389)).
  `Transition style: Instant` and `Fade` will look identical on such a light.
- Grouping this plugin's virtual player with a Chromecast/Google Home
  speaker requires that speaker's "Sendspin over Cast" output protocol
  (Settings -> Players -> the speaker -> advanced settings), which is
  itself experimental; some Cast devices don't switch protocols cleanly
  mid-group.

## Incubation history

This plugin was developed and tested against real hardware (Wi-Fi and
Zigbee strips via Home Assistant, a Chromecast-grouped speaker) as a
standalone pip package, [`ha-lights-sync`](https://github.com/zachfeldman/ha-lights-sync),
loaded via [`music-assistant-plugin-manager`](https://github.com/TigreGotico/music-assistant-plugin-manager)
before this PR. That repo is archived now that development has moved
in-tree; its `pre-fork-archive` branch preserves its final state (commit
history squashed into one commit per logical feature) if any of that
incremental development/debugging trail - per-entity adaptive pacing, the
Sendspin stuck-call wall-clock workaround in `_dispatch_send`, the
real-hardware test session that found and removed `energetic`/`auto`/
`strobe`, etc. - is useful during review.
