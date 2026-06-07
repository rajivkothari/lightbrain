"""
Enttec Pro / DMXking ultraDMX MAX USB-DMX output module.

This module is HARDWARE-READY but not yet tested — the DMXking ultraDMX MAX
adapter has been ordered and will be connected when it arrives.

Compatible with any Enttec USB Pro protocol device, including:
- Enttec USB DMX Pro (ENTTEC-70304)
- DMXking ultraDMX MAX (USB to DMX512 Pro adapter)

Enttec Pro frame format:
  0x7E          — Start of message label
  0x06          — Label: Output Only Send DMX Packet Request
  len_lo        — Data length low byte  (data = start_code + 512 channels)
  len_hi        — Data length high byte
  0x00          — DMX start code
  [512 bytes]   — DMX channel data (channels 1–512)
  0xE7          — End of message

Driver requirement — VCP, NOT D2XX
------------------------------------
This module uses pure pyserial over the OS Virtual COM Port (VCP) driver.
The DMXking (FTDI chip) must be enumerated as a VCP device:
  - Linux / Raspberry Pi: kernel ftdi_sio driver → /dev/ttyUSBx  (automatic)
  - macOS: Apple FTDI kext or FTDI VCP driver  → /dev/cu.usbserial-*
  - Windows: FTDI VCP driver (CDM) → COMx

Do NOT install FTDI's proprietary D2XX driver — it replaces the VCP entry
and makes the port unreachable to pyserial.  If the port disappears in
Device Manager after installing an FTDI driver, switch the device back to
VCP mode using FTDI's FT_Prog utility or by reinstalling the CDM VCP driver.

Do NOT run test_dmxking_rockwedge.py without a DMX fixture connected
and correctly addressed in the right channel mode.
"""

from typing import Optional, List
import serial
import serial.tools.list_ports
import time

from dmx.universe import DMXUniverse


ENTTEC_PRO_START = 0x7E
ENTTEC_PRO_END   = 0xE7
ENTTEC_PRO_LABEL = 0x06   # Send DMX Packet


def list_serial_ports() -> List[dict]:
    """Return a list of available serial ports as dicts."""
    ports = []
    for p in serial.tools.list_ports.comports():
        ports.append({
            "port":        p.device,
            "description": p.description,
            "hwid":        p.hwid,
        })
    return ports


def build_enttec_frame(universe: DMXUniverse) -> bytes:
    """
    Build a complete Enttec Pro USB DMX frame from a DMXUniverse.

    Returns bytes ready to write to the serial port.
    """
    dmx_data = bytes([0x00]) + universe.to_bytes()  # start code + 512 ch
    data_len = len(dmx_data)

    frame = bytearray()
    frame.append(ENTTEC_PRO_START)
    frame.append(ENTTEC_PRO_LABEL)
    frame.append(data_len & 0xFF)           # length low byte
    frame.append((data_len >> 8) & 0xFF)    # length high byte
    frame.extend(dmx_data)
    frame.append(ENTTEC_PRO_END)

    return bytes(frame)


class EnttecProOutput:
    """
    Serial DMX output for Enttec USB Pro compatible devices.

    Usage:
        out = EnttecProOutput()
        out.open("/dev/ttyUSB0")    # or "COM3" on Windows
        out.send(universe)
        out.close()                 # sends blackout before closing
    """

    def __init__(self):
        self._serial: Optional[serial.Serial] = None
        self._port:   Optional[str]            = None

    def open(self, port: str, baud_rate: int = 250000) -> None:
        """
        Open the serial connection to the DMXking / Enttec Pro device.

        Parameters are chosen for a plain VCP connection with no flow control:
          - rtscts / dsrdtr / xonxoff all False: explicitly disables all three
            flow-control modes so pyserial never toggles RTS/DTR lines or waits
            for CTS/DSR.  FTDI VCP adapters don't need handshaking; leaving these
            at their pyserial defaults can cause silent hangs on some platforms.
          - write_timeout=0.1: caps blocking I/O time at 100ms.  A 518-byte
            Enttec frame at 250 kbaud takes ~16ms; 100ms gives 6× headroom while
            ensuring the DMX daemon thread never stalls indefinitely if the USB
            cable is disconnected mid-write.

        Raises serial.SerialException if the port cannot be opened.
        """
        self._port   = port
        self._serial = serial.Serial(
            port          = port,
            baudrate      = baud_rate,
            bytesize      = serial.EIGHTBITS,
            parity        = serial.PARITY_NONE,
            stopbits      = serial.STOPBITS_TWO,
            timeout       = 0,        # non-blocking reads (we never read)
            write_timeout = 0.1,      # fail fast if USB stalls
            rtscts        = False,    # no RTS/CTS hardware flow control
            dsrdtr        = False,    # no DSR/DTR hardware flow control
            xonxoff       = False,    # no XON/XOFF software flow control
        )

    def send(self, universe: DMXUniverse) -> None:
        """Send a DMX universe frame to the device."""
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("Serial port not open — call open() first")
        frame = build_enttec_frame(universe)
        self._serial.write(frame)

    def blackout(self) -> None:
        """Send an all-zeros universe (safe state)."""
        if self._serial and self._serial.is_open:
            blank = DMXUniverse()
            self.send(blank)

    def close(self) -> None:
        """Send blackout then close the serial port."""
        try:
            self.blackout()
            time.sleep(0.05)  # allow last frame to flush
        except Exception:
            pass
        finally:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = None

    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> Optional[str]:
        return self._port

    @property
    def output_type(self) -> str:
        return "SERIAL"
