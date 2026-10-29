"""Observed profile-card ports, independent of the routing endpoint."""

from __future__ import annotations

from dataclasses import dataclass

import psutil

from ..schemas.instances import Instance
from ..schemas.profiles import ProfileData
from .ports import resolve_effective_port


@dataclass(frozen=True)
class ProfilePorts:
    ports: list[int]
    source: str
    reason: str | None = None


def listening_ports(instance: Instance) -> list[int]:
    """Read TCP listeners from a verified process tree, never unrelated PIDs."""
    expected_start = instance.process_start_time_ns
    if expected_start is None:
        return []
    try:
        root = psutil.Process(instance.pid)
        if int(root.create_time() * 1_000_000_000) != expected_start:
            return []
        try:
            children = root.children(recursive=True)
        except (psutil.Error, OSError):
            children = []
        ports: set[int] = set()
        for process in [root, *children]:
            try:
                if not process.is_running():
                    continue
                process_ports: set[int] = set()
                for connection in process.net_connections(kind="tcp"):
                    if connection.status == psutil.CONN_LISTEN and connection.laddr:
                        process_ports.add(connection.laddr.port)
                if process.is_running():
                    ports.update(process_ports)
            except (psutil.Error, OSError):
                continue
        # Discard the observation if the owning process exited during the scan.
        if not root.is_running() or int(root.create_time() * 1_000_000_000) != expected_start:
            return []
        return sorted(ports)
    except (psutil.Error, OSError):
        return []


def observe_instance_ports(instance: Instance) -> ProfilePorts:
    ports = listening_ports(instance)
    if ports:
        return ProfilePorts(ports, "process_sockets")
    if instance.port is not None:
        return ProfilePorts([instance.port], "launch_configuration")
    return ProfilePorts([], "unavailable", "no_listening_or_configured_port")


def configured_profile_ports(profile: ProfileData) -> ProfilePorts:
    if profile.port is not None:
        return ProfilePorts([profile.port], "profile_config")
    resolution = resolve_effective_port(profile.engine.id, profile.command)
    if resolution.source == "command" and resolution.port is not None:
        return ProfilePorts([resolution.port], resolution.source)
    return ProfilePorts(
        [resolution.port] if resolution.port is not None else [],
        resolution.source,
        resolution.reason,
    )
