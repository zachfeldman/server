"""
HA Lights Sync plugin for Music Assistant.

Each configured provider instance drives one group of Home Assistant
lights (e.g. "all the garage strips") as a single virtual Sendspin player -
group it with a real player in the Music Assistant UI the same way you'd
group a Hue Lights Sync player, and the configured lights react to
whatever that player plays, driven through Home Assistant's own
light.turn_on service rather than a device-specific protocol - works with
any light HA already controls (Kasa, Hue, LIFX, Zigbee, whatever).

Add the provider again (manifest declares multi_instance: true) for a
second independently-configured room/group.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from music_assistant.providers.ha_lights_sync.provider import HALightsSyncProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.enums import ProviderFeature
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

SUPPORTED_FEATURES: set[ProviderFeature] = set()


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return HALightsSyncProvider(mass, manifest, config, SUPPORTED_FEATURES)
