"""
Art-Net 4 UDP DMX output.

Implements the ArtDmx packet (OpCode 0x5000) from the Art-Net 4 specification.
Sends one 512-channel DMX universe per call to send_universe().

Compatible with any Art-Net node: Enttec ODE MK2, DMXking eDMX series,
CHAUVET D-Fi 2.4 GHz USB, and most professional lighting consoles.

Art-Net 4 spec reference: https://artisticlicence.com/WebSiteMaster/User%20Guides/art-net.pdf

Usage:
    out = ArtNetOutput(target_ip="192.168.1.255", universe=0)
    out.connect()
    out.send_universe([0] * 512)
    out.disconnect()

    # Or as a context manager:
    with ArtNetOutput("192.168.1.10") as out:
        out.send_universe(channels)

NOTE: Not tested against physical hardware — pending DMX-over-network test setup.
"""

import logging
import socket
import struct
from typing import List, Optional

log = logging.getLogger(__name__)

ARTNET_PORT   = 6454
ARTNET_ID     = b"Art-Net\x00"   # 8-byte magic ID (spec §7)
ARTDMX_OPCODE = 0x5000            # ArtDmx OpCode (little-endian in packet)
PROTOCOL_VER  = 14                # Art-Net protocol version 14


class ArtNetOutput:
    """
    Unicast or broadcast Art-Net 4 ArtDmx sender.

    Parameters
    ----------
    target_ip   IP address or broadcast address of the Art-Net node.
                Use "2.255.255.255" for limited broadcast (all local nodes).
                Use a specific IP (e.g. "192.168.1.50") for unicast.
    universe    15-bit Art-Net universe index (0–32767, default 0).
    """

    def __init__(
        self,
        target_ip: str = "2.255.255.255",
        universe:  int = 0,
    ):
        if not 0 <= universe <= 32767:
            raise ValueError(f"Art-Net universe must be 0–32767, got {universe}")
        self._ip       = target_ip
        self._universe = universe & 0x7FFF
        self._sequence = 0
        self._sock: Optional[socket.socket] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the UDP socket. Safe to call multiple times."""
        if self._sock is not None:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            log.info("ArtNetOutput connected → %s:%d (universe %d)",
                     self._ip, ARTNET_PORT, self._universe)
        except OSError as e:
            log.warning("ArtNetOutput: could not open socket: %s", e)
            self._sock = None

    def disconnect(self) -> None:
        """Send blackout and close the UDP socket."""
        if self._sock is not None:
            try:
                self.send_universe([0] * 512)
            except Exception:
                pass
            self._sock.close()
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def send_universe(self, channels: List[int]) -> None:
        """
        Send a 512-channel DMX universe as an ArtDmx packet.

        channels — list of 512 integers 0–255 (extra values are ignored,
                   missing values are padded with 0).
        """
        if self._sock is None:
            return

        # Clamp and pad to exactly 512 bytes
        dmx_data = bytes(
            max(0, min(255, int(v))) for v in (list(channels) + [0] * 512)
        )[:512]

        packet = self._build_artdmx(dmx_data)
        try:
            self._sock.sendto(packet, (self._ip, ARTNET_PORT))
        except OSError as e:
            log.warning("ArtNetOutput send failed: %s", e)

        self._sequence = (self._sequence + 1) % 256

    # ------------------------------------------------------------------
    # Packet builder (Art-Net 4 §9 ArtDmx)
    # ------------------------------------------------------------------

    def _build_artdmx(self, dmx_data: bytes) -> bytes:
        """
        Build an ArtDmx packet.

        Packet layout (Art-Net 4 spec §9.1):
          Byte  0- 7  ID          "Art-Net\\0"
          Byte  8- 9  OpCode      0x5000 little-endian
          Byte 10-11  ProtVer     0x000E big-endian
          Byte 12     Sequence    0 = no sequence, 1–255 sequential
          Byte 13     Physical    0 (unused in most nodes)
          Byte 14-15  Universe    15-bit universe, little-endian
          Byte 16-17  Length      number of DMX slots, big-endian (must be even)
          Byte 18+    Data        DMX slot values (start code already removed)
        """
        length = len(dmx_data)
        # Art-Net spec requires length to be an even number ≥ 2
        if length % 2 != 0:
            dmx_data = dmx_data + b"\x00"
            length  += 1

        return (
            ARTNET_ID
            + struct.pack("<H",  ARTDMX_OPCODE)          # OpCode LE
            + struct.pack(">H",  PROTOCOL_VER)            # ProtVer BE
            + bytes([self._sequence])                     # Sequence
            + b"\x00"                                     # Physical
            + struct.pack("<H",  self._universe)          # Universe LE
            + struct.pack(">H",  length)                  # Length BE
            + dmx_data                                    # DMX payload
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def target_ip(self) -> str:
        return self._ip

    @property
    def universe(self) -> int:
        return self._universe

    def reopen(self) -> None:
        """Close and re-open the UDP socket (called by DmxOutputThread on repeated failures)."""
        self.disconnect()
        self.connect()

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    @property
    def sequence(self) -> int:
        return self._sequence
