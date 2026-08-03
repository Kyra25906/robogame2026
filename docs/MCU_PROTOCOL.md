# MCU protocol integration contract

The core frame implementation is in `robogame_core.serial_protocol`.

## Frame

All multi-byte numbers are little-endian.

```text
AA 55 | version:u8 | type:u8 | sequence:u16 | payload_length:u16 | payload | crc16:u16
```

- CRC: CRC-16/CCITT-FALSE, polynomial `0x1021`, initial value `0xFFFF`.
- Maximum payload: 512 bytes.
- The receiver must discard corrupt frames and resynchronize on `AA 55`.
- Sequence numbers wrap at 65535.

## Assigned message types

| Type | Direction | Payload |
|---|---|---|
| `0x01` | upper → MCU | `vx:f32, vy:f32, wz:f32, mode:u8` |
| `0x02` | upper → MCU | mechanism command; freeze with electrical team before use |
| `0x03` | both | heartbeat |
| `0x81` | MCU → upper | robot status; freeze fields before real-mode enablement |
| `0x82` | MCU → upper | wheel odometry |
| `0x83` | MCU → upper | IMU sample |

## Mandatory MCU behavior

- If a valid velocity command is not received for 150 ms, command all chassis velocities to zero.
- Never resume an old command after reconnection.
- Hardware emergency stop directly disables every actuator.
- Reject non-finite velocities and commands outside electrical limits.
- Return a completion or failure result for every mechanism command.

The current bridge transmits `0x01`. Incoming real-hardware payload decoding must be
implemented after the electrical team signs the exact field table; mock mode is used until then.

