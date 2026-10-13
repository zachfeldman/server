"""Tests for the HA Lights audio analyzer - pure logic, no Sendspin/hardware needed."""

from __future__ import annotations

import pytest

from music_assistant.providers.ha_lights_sync.analyzer import (
    _BEAT_FLASH_DECAY_S,
    _MIN_FLASH_DECAY_S,
    HALightsAudioAnalyzer,
    _clamp01,
)
from music_assistant.providers.ha_lights_sync.constants import (
    AUTO_MIN_DWELL_S,
    AUTO_MODE_CANDIDATES,
    RENDER_PERIOD_S,
)


def test_clamp01() -> None:
    """Clamp a float into the 0-1 range."""
    assert _clamp01(-1.0) == 0.0
    assert _clamp01(0.5) == 0.5
    assert _clamp01(2.0) == 1.0


def test_render_without_input_stays_within_range() -> None:
    """A render with no spectrum/beat input yet still returns a valid command."""
    analyzer = HALightsAudioAnalyzer(color_mode="smooth", brightness=100)
    command = analyzer.render(now_s=0.0)
    assert 1 <= command.brightness <= 100
    assert 0 <= command.saturation <= 100
    assert 0 <= command.hue < 360
    assert command.transition_ms > 0


def test_loud_bass_raises_brightness_over_silence() -> None:
    """Louder bass input renders a brighter command than silence."""
    quiet = HALightsAudioAnalyzer(color_mode="smooth", brightness=100)
    loud = HALightsAudioAnalyzer(color_mode="smooth", brightness=100)

    # Feed enough frames for the asymmetric EMA to actually rise.
    for _ in range(10):
        quiet.apply_spectrum([0.0] * 12)
        loud.apply_spectrum([1.0] * 12)

    quiet_cmd = quiet.render(now_s=1.0)
    loud_cmd = loud.render(now_s=1.0)
    assert loud_cmd.brightness > quiet_cmd.brightness


def test_beat_triggers_a_brightness_flash() -> None:
    """A scheduled beat raises brightness above the resting level when it fires."""
    analyzer = HALightsAudioAnalyzer(color_mode="flashing", brightness=100)
    analyzer.apply_spectrum([0.1] * 12)
    baseline = analyzer.render(now_s=0.0).brightness

    analyzer.push_beats([(1.0, False)])
    at_beat = analyzer.render(now_s=1.0).brightness
    assert at_beat > baseline


def test_stale_beats_are_dropped_not_fired_late() -> None:
    """A beat scheduled far enough in the past is dropped rather than fired late."""
    analyzer = HALightsAudioAnalyzer(color_mode="flashing", brightness=100)
    analyzer.push_beats([(0.0, False)])
    # Jump far enough ahead that the beat is stale (see _BEAT_STALE_S).
    analyzer.render(now_s=10.0)
    assert analyzer._beats == []


def test_brightness_respects_configured_ceiling() -> None:
    """Brightness never renders above the configured ceiling, even under loud bass."""
    capped = HALightsAudioAnalyzer(color_mode="smooth", brightness=10)
    for _ in range(10):
        capped.apply_spectrum([1.0] * 12)
    command = capped.render(now_s=1.0)
    assert command.brightness <= 10


def test_update_settings_changes_active_mode() -> None:
    """update_settings changes the active color_mode and brightness ceiling live."""
    analyzer = HALightsAudioAnalyzer(color_mode="smooth", brightness=100)
    analyzer.update_settings(color_mode="flashing", brightness=50)
    assert analyzer.color_mode == "flashing"
    assert analyzer.brightness_ceiling == 50


def test_unknown_color_mode_falls_back_to_default() -> None:
    """An unrecognized color_mode falls back to the default rather than erroring."""
    analyzer = HALightsAudioAnalyzer(color_mode="not-a-real-mode", brightness=100)
    assert analyzer.color_mode == "smooth"


@pytest.mark.parametrize("mode", ["smooth", "ambient", "flashing", "energetic", "pulse", "auto", "strobe"])
def test_every_preset_renders_without_error(mode: str) -> None:
    """Every color_mode renders a valid command given spectrum and beat input."""
    analyzer = HALightsAudioAnalyzer(color_mode=mode, brightness=100)
    analyzer.apply_spectrum([0.3] * 12)
    analyzer.push_beats([(0.5, True)])
    command = analyzer.render(now_s=0.5)
    assert 1 <= command.brightness <= 100


# -- Speed (beat_multiplier) --


def test_1x_speed_inserts_no_subbeats() -> None:
    """At 1x speed, only the real tracked beats are scheduled."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=1)
    analyzer.push_beats([(0.0, False), (1.0, False), (2.0, False)])
    assert [b.timestamp_s for b in analyzer._beats] == [0.0, 1.0, 2.0]


def test_2x_speed_inserts_one_subbeat_per_gap() -> None:
    """At 2x speed, one evenly-spaced sub-beat is inserted between each pair of real beats."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=2)
    analyzer.push_beats([(0.0, False), (1.0, False)])
    timestamps = [b.timestamp_s for b in analyzer._beats]
    assert timestamps == pytest.approx([0.0, 0.5, 1.0])


def test_4x_speed_inserts_three_subbeats_per_gap() -> None:
    """At 4x speed, three evenly-spaced sub-beats are inserted between each pair of real beats."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=4)
    analyzer.push_beats([(0.0, False), (1.0, False)])
    timestamps = [b.timestamp_s for b in analyzer._beats]
    assert timestamps == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])


def test_subbeats_are_weaker_than_the_real_beat_they_came_from() -> None:
    """A synthesized sub-beat has a weaker flash strength than the real beat it interpolates."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=2)
    analyzer.push_beats([(0.0, False), (1.0, False)])
    real, sub, _next_real = analyzer._beats
    assert sub.strength < real.strength


def test_a_single_beat_with_no_following_beat_still_schedules_cleanly() -> None:
    """A lone beat with no following beat to interpolate toward still schedules at full strength."""
    # No "next" beat to interpolate toward - must not crash, and the lone
    # beat keeps full strength (nothing to subdivide into).
    analyzer = HALightsAudioAnalyzer(beat_multiplier=4)
    analyzer.push_beats([(0.0, False)])
    assert len(analyzer._beats) == 1
    assert analyzer._beats[0].strength == 1.0


def test_fast_subbeats_get_a_tighter_flash_decay_than_the_default() -> None:
    """Closely-spaced sub-beats get a tightened flash decay so fast pulses stay distinct."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=4)
    # 0.2s apart overall -> 0.05s between each of the 4 sub-intervals, well
    # under the default decay, so every beat here should be tightened.
    analyzer.push_beats([(0.0, False), (0.2, False)])
    for beat in analyzer._beats[:-1]:
        assert _MIN_FLASH_DECAY_S <= beat.decay_s <= _BEAT_FLASH_DECAY_S


def test_invalid_beat_multiplier_falls_back_to_default() -> None:
    """An unrecognized beat_multiplier falls back to the default (1x)."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=3)
    assert analyzer.beat_multiplier == 1


def test_update_settings_changes_beat_multiplier() -> None:
    """update_settings changes the active beat_multiplier live."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=1)
    analyzer.update_settings(beat_multiplier=4)
    assert analyzer.beat_multiplier == 4


def test_update_settings_ignores_invalid_beat_multiplier() -> None:
    """update_settings ignores an invalid beat_multiplier rather than applying it."""
    analyzer = HALightsAudioAnalyzer(beat_multiplier=2)
    analyzer.update_settings(beat_multiplier=3)
    assert analyzer.beat_multiplier == 2


@pytest.mark.parametrize("multiplier", [1, 2, 4])
def test_every_speed_renders_without_error(multiplier: int) -> None:
    """Every beat_multiplier value renders a valid command given spectrum and beat input."""
    analyzer = HALightsAudioAnalyzer(color_mode="flashing", beat_multiplier=multiplier)
    analyzer.apply_spectrum([0.3] * 12)
    analyzer.push_beats([(0.5, True), (1.5, False)])
    command = analyzer.render(now_s=0.5)
    assert 1 <= command.brightness <= 100


# -- Transition style --


def test_fade_is_the_default_and_spans_the_render_period() -> None:
    """The default transition_style is 'fade', spanning one render period."""
    analyzer = HALightsAudioAnalyzer()
    assert analyzer.transition_style == "fade"
    command = analyzer.render(now_s=0.0)
    assert command.transition_ms == round(RENDER_PERIOD_S * 1000)


def test_instant_style_sends_zero_transition() -> None:
    """The 'instant' transition_style sends a zero-length transition."""
    analyzer = HALightsAudioAnalyzer(transition_style="instant")
    command = analyzer.render(now_s=0.0)
    assert command.transition_ms == 0


def test_invalid_transition_style_falls_back_to_fade() -> None:
    """An unrecognized transition_style falls back to 'fade'."""
    analyzer = HALightsAudioAnalyzer(transition_style="teleport")
    assert analyzer.transition_style == "fade"


def test_update_settings_changes_transition_style() -> None:
    """update_settings changes the active transition_style live."""
    analyzer = HALightsAudioAnalyzer(transition_style="fade")
    analyzer.update_settings(transition_style="instant")
    assert analyzer.transition_style == "instant"
    assert analyzer.render(now_s=0.0).transition_ms == 0


def test_update_settings_ignores_invalid_transition_style() -> None:
    """update_settings ignores an invalid transition_style rather than applying it."""
    analyzer = HALightsAudioAnalyzer(transition_style="instant")
    analyzer.update_settings(transition_style="teleport")
    assert analyzer.transition_style == "instant"


# -- flashing's strobe contrast (bass_weight) --


def test_flashing_stays_dark_between_beats_even_under_loud_continuous_bass() -> None:
    """
    Regression guard for the "not really super flashing" tuning fix.

    Before bass_weight existed, resting brightness rode the continuous bass
    level up just like every other mode, so a loud sustained passage kept
    flashing's "resting" state nearly as bright as its on-beat flash - no
    real strobe contrast. flashing should stay close to its floor between
    beats regardless of how loud the track currently is; smooth should not
    (that continuous swell is its whole character).
    """
    flashing = HALightsAudioAnalyzer(color_mode="flashing", brightness=100)
    smooth = HALightsAudioAnalyzer(color_mode="smooth", brightness=100)
    for _ in range(20):
        flashing.apply_spectrum([1.0] * 12)  # loud, continuous bass
        smooth.apply_spectrum([1.0] * 12)

    # No push_beats() call - this is deliberately the "between beats" case.
    flashing_level = flashing.render(now_s=5.0).brightness
    smooth_level = smooth.render(now_s=5.0).brightness
    assert flashing_level < smooth_level


@pytest.mark.parametrize("mode", ["smooth", "ambient", "flashing", "energetic"])
def test_every_preset_flash_is_clearly_brighter_than_its_own_resting_level(mode: str) -> None:
    """
    Every preset's on-beat flash should read as distinctly brighter than resting.

    "pulse" is deliberately excluded - its whole point is brightness driven
    purely by continuous overall loudness with flash_strength=0 (no beat
    flash at all), see test_pulse_mode_has_no_beat_flash below.
    """
    analyzer = HALightsAudioAnalyzer(color_mode=mode, brightness=100)
    for _ in range(10):
        analyzer.apply_spectrum([0.4] * 12)
    resting = analyzer.render(now_s=1.0).brightness

    analyzer.push_beats([(2.0, False)])
    at_beat = analyzer.render(now_s=2.0).brightness
    assert at_beat > resting


# -- Sensitivity --


def test_sensitivity_amplifies_a_quiet_signal() -> None:
    """A higher Sensitivity renders brighter than the default given the same quiet input."""
    quiet_default = HALightsAudioAnalyzer(color_mode="smooth", brightness=100, sensitivity=100)
    quiet_boosted = HALightsAudioAnalyzer(color_mode="smooth", brightness=100, sensitivity=300)

    # Same, deliberately quiet input to both.
    for _ in range(15):
        quiet_default.apply_spectrum([0.1] * 12)
        quiet_boosted.apply_spectrum([0.1] * 12)

    default_brightness = quiet_default.render(now_s=2.0).brightness
    boosted_brightness = quiet_boosted.render(now_s=2.0).brightness
    assert boosted_brightness > default_brightness


def test_update_settings_changes_sensitivity() -> None:
    """update_settings changes the active sensitivity multiplier live."""
    analyzer = HALightsAudioAnalyzer(sensitivity=100)
    analyzer.update_settings(sensitivity=250)
    assert analyzer._sensitivity == pytest.approx(2.5)


def test_default_sensitivity_is_unity_gain() -> None:
    """The default sensitivity (100%) is a no-op multiplier on the raw spectrum."""
    # 100% should be a no-op multiplier - confirms the default doesn't
    # silently change behavior for anyone who never touches this setting.
    analyzer = HALightsAudioAnalyzer(sensitivity=100)
    analyzer.apply_spectrum([0.5] * 12)
    assert analyzer._bass.value == pytest.approx(0.5 * 0.6)


# -- Pulse mode (brightness tracks overall loudness) --


def test_pulse_mode_has_no_beat_flash() -> None:
    """Pulse mode's brightness does not change at all when a beat fires."""
    analyzer = HALightsAudioAnalyzer(color_mode="pulse", brightness=100)
    for _ in range(10):
        analyzer.apply_spectrum([0.4] * 12)
    resting = analyzer.render(now_s=1.0).brightness

    analyzer.push_beats([(2.0, False)])
    at_beat = analyzer.render(now_s=2.0).brightness
    assert at_beat == resting


def test_pulse_mode_tracks_overall_level_not_just_bass() -> None:
    """
    Pulse reacts to loud mid/treble content even with zero bass.

    That's the thing that distinguishes it from smooth/ambient/energetic,
    which only ever look at the bass band (see _ModePreset.use_overall_level).
    """
    pulse = HALightsAudioAnalyzer(color_mode="pulse", brightness=100)
    smooth = HALightsAudioAnalyzer(color_mode="smooth", brightness=100)

    # Zero bass (first third of bins), loud everything else.
    bins = [0.0, 0.0, 0.0, 0.0] + [1.0] * 8
    for _ in range(15):
        pulse.apply_spectrum(bins)
        smooth.apply_spectrum(bins)

    pulse_brightness = pulse.render(now_s=2.0).brightness
    smooth_brightness = smooth.render(now_s=2.0).brightness
    assert pulse_brightness > smooth_brightness


def test_pulse_mode_brightness_rises_and_falls_with_overall_level() -> None:
    """Pulse mode's brightness rises with overall loudness and falls back down with it."""
    analyzer = HALightsAudioAnalyzer(color_mode="pulse", brightness=100)
    for _ in range(15):
        analyzer.apply_spectrum([0.0] * 12)
    quiet_brightness = analyzer.render(now_s=1.0).brightness

    for _ in range(15):
        analyzer.apply_spectrum([1.0] * 12)
    loud_brightness = analyzer.render(now_s=2.0).brightness
    assert loud_brightness > quiet_brightness


# -- Auto mode --


def test_auto_mode_picks_a_low_energy_preset_for_quiet_audio() -> None:
    """Auto mode picks the lowest-energy candidate (ambient) for quiet audio."""
    analyzer = HALightsAudioAnalyzer(color_mode="auto", brightness=100)
    for _ in range(15):
        analyzer.apply_spectrum([0.0] * 12)
    analyzer.render(now_s=1.0)
    assert analyzer._auto_mode == "ambient"


def test_auto_mode_picks_a_high_energy_preset_for_loud_audio() -> None:
    """Auto mode picks the highest-energy candidate (flashing) for loud audio."""
    analyzer = HALightsAudioAnalyzer(color_mode="auto", brightness=100)
    for _ in range(15):
        analyzer.apply_spectrum([1.0] * 12)
    # First classification ever - takes effect immediately, no dwell wait.
    analyzer.render(now_s=1.0)
    assert analyzer._auto_mode == "flashing"


def test_auto_mode_does_not_flap_before_the_min_dwell_time() -> None:
    """Auto mode doesn't switch presets again until the minimum dwell time has elapsed."""
    analyzer = HALightsAudioAnalyzer(color_mode="auto", brightness=100)
    for _ in range(15):
        analyzer.apply_spectrum([0.0] * 12)
    analyzer.render(now_s=0.0)
    assert analyzer._auto_mode == "ambient"

    # Loud now, but well before the dwell window closes - must not switch yet.
    for _ in range(15):
        analyzer.apply_spectrum([1.0] * 12)
    analyzer.render(now_s=AUTO_MIN_DWELL_S / 2)
    assert analyzer._auto_mode == "ambient"

    # Still loud, now past the dwell window - free to switch.
    analyzer.render(now_s=AUTO_MIN_DWELL_S + 1.0)
    assert analyzer._auto_mode == "flashing"


def test_auto_mode_never_selects_strobe() -> None:
    """Strobe is never one of auto mode's candidate presets."""
    assert "strobe" not in {name for name, _ in AUTO_MODE_CANDIDATES}


# -- Strobe mode --


def test_strobe_is_off_between_beats() -> None:
    """Strobe mode is off (a real light.turn_off) between beats, regardless of bass level."""
    analyzer = HALightsAudioAnalyzer(color_mode="strobe", brightness=100)
    analyzer.apply_spectrum([1.0] * 12)  # loud, continuous - must not matter for strobe
    command = analyzer.render(now_s=5.0)  # no beat scheduled near this timestamp
    assert command.on is False


def test_strobe_is_on_right_after_a_beat() -> None:
    """Strobe mode is on at full brightness right when a beat fires."""
    analyzer = HALightsAudioAnalyzer(color_mode="strobe", brightness=100)
    analyzer.push_beats([(1.0, False)])
    command = analyzer.render(now_s=1.0)
    assert command.on is True
    assert command.brightness == 100


def test_strobe_turns_back_off_after_the_flash_decays() -> None:
    """Strobe mode turns back off once a beat's flash-decay window has passed."""
    analyzer = HALightsAudioAnalyzer(color_mode="strobe", brightness=100)
    analyzer.push_beats([(1.0, False)])
    analyzer.render(now_s=1.0)
    # Well past the (short) flash decay window, no new beat scheduled.
    command = analyzer.render(now_s=10.0)
    assert command.on is False


def test_strobe_ignores_transition_style() -> None:
    """Strobe mode always sends a zero-length transition, regardless of transition_style."""
    analyzer = HALightsAudioAnalyzer(color_mode="strobe", transition_style="fade")
    analyzer.push_beats([(1.0, False)])
    command = analyzer.render(now_s=1.0)
    assert command.transition_ms == 0


def test_other_modes_are_always_on() -> None:
    """Every mode other than strobe always renders a command with on=True."""
    for mode in ("smooth", "ambient", "flashing", "energetic", "pulse", "auto"):
        analyzer = HALightsAudioAnalyzer(color_mode=mode)
        assert analyzer.render(now_s=0.0).on is True


# -- Hue lock --


def test_hue_lock_overrides_beat_driven_hue_jump() -> None:
    """Hue lock overrides the hue even on a downbeat, which would otherwise jump it the most."""
    analyzer = HALightsAudioAnalyzer(color_mode="smooth", hue_lock_enabled=True, hue_lock_deg=200)
    analyzer.push_beats([(1.0, True)])  # downbeat - largest possible hue jump
    command = analyzer.render(now_s=1.0)
    assert command.hue == 200


def test_hue_lock_overrides_treble_driven_hue_shift() -> None:
    """Hue lock overrides the hue even under loud treble, which would otherwise shift it."""
    analyzer = HALightsAudioAnalyzer(color_mode="smooth", hue_lock_enabled=True, hue_lock_deg=90)
    for _ in range(15):
        analyzer.apply_spectrum([1.0] * 12)  # loud treble, would normally shift hue
    command = analyzer.render(now_s=1.0)
    assert command.hue == 90


def test_hue_lock_disabled_lets_hue_vary() -> None:
    """With hue lock disabled, a downbeat is free to move the hue off its starting value."""
    analyzer = HALightsAudioAnalyzer(color_mode="smooth", hue_lock_enabled=False)
    analyzer.push_beats([(1.0, True)])
    command = analyzer.render(now_s=1.0)
    assert command.hue != 0  # the downbeat jump should have moved it off its 0.0 start


def test_update_settings_can_enable_and_disable_hue_lock() -> None:
    """update_settings can enable hue lock and later disable it again, live."""
    analyzer = HALightsAudioAnalyzer(color_mode="smooth")
    analyzer.update_settings(hue_lock_enabled=True, hue_lock_deg=42)
    assert analyzer.render(now_s=0.0).hue == 42

    analyzer.update_settings(hue_lock_enabled=False)
    analyzer.push_beats([(1.0, True)])
    command = analyzer.render(now_s=1.0)
    assert command.hue != 42


def test_hue_lock_works_with_strobe_mode_too() -> None:
    """Hue lock applies to strobe mode's hue output too, not just the continuous modes."""
    analyzer = HALightsAudioAnalyzer(color_mode="strobe", hue_lock_enabled=True, hue_lock_deg=15)
    analyzer.push_beats([(1.0, False)])
    command = analyzer.render(now_s=1.0)
    assert command.hue == 15
