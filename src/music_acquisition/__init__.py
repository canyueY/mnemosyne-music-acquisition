# -*- coding: utf-8 -*-
"""Mnemosyne Music Acquisition — Independent Music Acquisition Infrastructure.

Engine is host-agnostic (DSH or Mnemosyne Node). Not part of Cognitive Core.
"""

from __future__ import annotations

from music_acquisition.version import __version__
from music_acquisition.engine.music_acquisition_engine import MusicAcquisitionEngine
from music_acquisition.hosts.in_memory import InMemoryHostRuntime
from music_acquisition.hosts.mnemosyne_node import MnemosyneNodeHostRuntime, open_mnemosyne_host

__all__ = [
    "MusicAcquisitionEngine",
    "InMemoryHostRuntime",
    "MnemosyneNodeHostRuntime",
    "open_mnemosyne_host",
    "__version__",
]
