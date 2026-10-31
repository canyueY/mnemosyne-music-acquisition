# -*- coding: utf-8 -*-
from music_acquisition.source.netease.adapter import NeteaseSourceAdapter

NETEASE_ADAPTER_STATUS = "phase_2_http_config_bind"
MIGRATION_SOURCE = "Murasame/netease_music.py web APIs → NeteaseSourceAdapter"

__all__ = ["NeteaseSourceAdapter", "NETEASE_ADAPTER_STATUS", "MIGRATION_SOURCE"]
