# -*- coding: utf-8 -*-
"""DSH Host adapter stub (Mode A) — for future dsh-mnemosyne-music plugin.

Must not import DSH internals into Engine. Plugin implements HostRuntime only.
"""

from __future__ import annotations

from music_acquisition.hosts.in_memory import InMemoryHostRuntime as DshHostRuntimeStub

__all__ = ["DshHostRuntimeStub"]
