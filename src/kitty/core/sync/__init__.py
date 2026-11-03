"""Peer sync between one person's rika! devices (docs/platform-design.md, Q16).

- ``noise``: the Noise protocol framework (IK for sync, XXpsk3 for pairing) over the
  ``cryptography`` primitives rika! already locks.
- ``channel``: framing, the encrypted channel, sync handshakes and code pairing (SPAKE2).
- ``discovery``: LAN discovery over mDNS/DNS-SD (``_kitty-sync._tcp``).
- ``peers``: this device's identity and the trust list of paired devices.
- ``protocol``: version-vector exchange and Loro update transfer per page.
- ``service``: the listener, the scheduler and the state Settings → Devices shows.
"""
