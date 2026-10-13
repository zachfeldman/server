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
HALightGroupBridge -> hass provider's HomeAssistantClient -> light.turn_on/turn_off
       |
       v (Home Assistant's own websocket API, then whatever integration owns the entity)
Home Assistant -> the physical light (Kasa, Hue, Zigbee, LIFX, whatever)
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
| **Energetic** | Big brightness swings on hits plus fast color rotation. |
| **Pulse** | Brightness continuously tracks the track's overall loudness (full spectrum, not just bass); no beat flash. |
| **Auto** | Dynamically picks between Ambient/Smooth/Energetic/Flashing based on the track's current energy, debounced so it doesn't flap at a boundary. Never selects Strobe. |
| **Strobe** | Hard on/off toggle - a real `light.turn_off` between beats, full brightness during the flash window. ⚠️ Combined with a high Speed setting on a fast track this can produce rapid, high-contrast flashing; not recommended for anyone with photosensitive epilepsy. |

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

This plugin was developed and tested against real hardware (TP-Link Kasa
strips via Home Assistant, a Chromecast-grouped speaker) as a standalone
pip package, [`ha-lights-sync`](https://github.com/zachfeldman/ha-lights-sync),
loaded via [`music-assistant-plugin-manager`](https://github.com/TigreGotico/music-assistant-plugin-manager)
before this PR. That repo's commit history has the full incremental
development/debugging trail (per-entity adaptive pacing, the Sendspin
stuck-call wall-clock workaround in `_dispatch_send`, tuning each mode's
strobe contrast, etc.) if any of that context is useful during review.
