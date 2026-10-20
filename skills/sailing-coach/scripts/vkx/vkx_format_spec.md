# Vakaros `.vkx` File Format (Reverse-Engineered)

**Status:** Unofficial, reverse-engineered specification
**Source material:** Two Vakaros Atlas 2 logs from the boat *Zero Gravity* (sail 1216): `Zero Gravity 9-26-2026.vkx` (2,876,048 bytes, ~8.0 h) and `Zero Gravity 9-27-2026.vkx` (2,484,812 bytes, ~6.6 h), firmware unknown. Semantics cross-checked against Njord Analytics exports for the same regatta (Sept ODW 2026, San Diego): per-second tracks, course geometry, and event lists for three boats including 1216.
**Confidence:** The structural layout below parses both files end-to-end with zero leftover or misaligned bytes. Fields validated against the Njord export are marked ✓; fields marked ⚠ remain inferred and unconfirmed.

---

## 1. Overall structure

A `.vkx` file is a flat sequence of **pages**. There is no global file header beyond the first page's header.

```
┌────────────┬──────────────────────────────┬────────────┐
│ 0xFF page  │ data records                 │ 0xFE page  │
│ header     │ (0x02, 0x03, 0x04, 0x05, …)  │ footer     │
└────────────┴──────────────────────────────┴────────────┘
   repeated until end of file
```

- Pages are approximately 2 KB but **variable length** (1,457–2,052 bytes observed across the two files, including header and footer).
- Records **do not** span page boundaries in the observed files; a page footer is always followed immediately by the next page header (or end of file).
- Everything is **little-endian**.
- There is no alignment or padding between records.

### Record framing

Every record is:

```
┌────────────┬───────────────────────────────┐
│ u8 key     │ fixed-size payload            │
└────────────┴───────────────────────────────┘
```

The key byte determines the payload size (see table below). There are no length fields on data records, so a parser **must** know every key's size; a single unknown key makes the rest of the stream unreadable. Parsers should abort with the file offset on an unknown key rather than guess.

### Timestamps

Every data record's payload begins with:

| Type | Meaning |
|------|---------|
| `u64` | Milliseconds since the Unix epoch, **UTC** |

Timestamps are non-decreasing across the file.

---

## 2. Record type summary

| Key | Payload size (bytes, excl. key) | Name | Count (9-26 file) | Count (9-27 file) |
|------|------|------|------|------|
| `0xFF` | 7 | Page header | 1,416 | 1,223 |
| `0xFE` | 2 | Page footer | 1,416 | 1,223 |
| `0x02` | 44 | Position / velocity / orientation | 57,639 | 47,748 |
| `0x03` | 20 | Periodic status A ⚠ | 481 | 397 |
| `0x04` | 13 | Race-timer event | 16 | 8 |
| `0x05` | 17 | Start-line end position | 13,893 | 17,168 |
| `0x07` | 12 | Battery status | 481 | 397 |
| `0x08` | 13 | One-shot status ⚠ | 1 | 1 |
| `0x0E` | 16 | Session marker ⚠ | 3 | 3 |

Other keys presumably exist on other firmware versions / device configurations (e.g. there were no wind-sensor or speed-through-water records in the source file, suggesting instrument-dependent record types are omitted when not present).

---

## 3. Record layouts

Offsets are relative to the start of the payload (i.e. after the key byte).

### `0xFF` — Page header (7 bytes)

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u8` | version? ⚠ | `0x05` in every page of both files |
| 1 | `u16` | unknown ⚠ | `0x0000` in every page |
| 3 | `u32` | page counter ✓ | Increments by 1 per page. Confirmed to be a **device-lifetime** counter: the 9-26 file ends at `0x0000CDAD` and the 9-27 file begins at `0x0000CDAE`, exactly contiguous |

### `0xFE` — Page footer (2 bytes)

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u16` | page length ✓ | Total byte length of the page just ended, **including** its `0xFF` header and this footer. Confirmed as a length (not a checksum): page lengths vary (1,457–2,052) and the footer matches the actual length for 2,634 of 2,639 pages across both files. The 5 mismatching pages all contain an `0x04` race-timer record and all over-state the length by exactly 53 bytes — treat the footer as advisory, not as framing |

### `0x02` — Position / velocity / orientation (44 bytes)

Logged at a steady **2 Hz**. The primary track record.

| Offset | Type | Field | Units / notes |
|--------|------|-------|---------------|
| 0 | `u64` | timestamp | ms since epoch, UTC |
| 8 | `i32` | latitude | degrees × 10⁷ |
| 12 | `i32` | longitude | degrees × 10⁷ |
| 16 | `f32` | SOG ✓ | speed over ground, m/s (matches Njord's knots column after × 1.94384) |
| 20 | `f32` | COG ✓ | course over ground, **radians**, 0 … 2π, **degrees true** after conversion (matches Njord `COG`) |
| 24 | `f32` | unknown ⚠ | Constant for long stretches, changes occasionally (e.g. `537341.8`, `1085683.75`). Not plausible as altitude; possibly an internal counter or accuracy/DOP encoding |
| 28 | `f32` | quaternion w ✓ | unit orientation quaternion |
| 32 | `f32` | quaternion x ✓ | |
| 36 | `f32` | quaternion y ✓ | |
| 40 | `f32` | quaternion z ✓ | |

Quaternion notes (validated against Njord's per-second export for the same boat): the values are unit-norm, and a standard aerospace **ZYX (yaw-pitch-roll) Euler conversion** gives:

- **yaw = magnetic heading** (matches Njord `Heading_Mag`; Njord's true `Heading` = yaw + magnetic variation, 10.7–10.8°E in San Diego),
- **roll = heel**, same sign convention as Njord (negative on starboard tack),
- **pitch = trim** (fore-aft), same sign as Njord's `Trim`.

Instantaneous 2 Hz values differ from Njord's rows by up to a couple of degrees because Njord exports per-second averages.

### `0x03` — Periodic status A (20 bytes) ⚠

Logged once per minute, always paired with an `0x07` record at the same timestamp.

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u64` | timestamp | ms since epoch, UTC |
| 8 | `f32` | unknown ⚠ | Small value oscillating in a narrow band (0.1901–0.1905 across both files, both days). Not monotonic, so not an accumulator; possibly a sensor reading or calibration scalar |
| 12 | `i32` | latitude | degrees × 10⁷ (matches current position) |
| 16 | `i32` | longitude | degrees × 10⁷ |

### `0x04` — Race-timer event (13 bytes)

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u64` | timestamp | ms since epoch, UTC |
| 8 | `u8` | event code | see below |
| 9 | `u32` | value | meaning depends on event code |

Observed event codes, deciphered by correlating both files against the Njord Analytics event export for the same regatta (every `0x04` record in both files matched a Njord event, same values, within 0.001–1.3 s):

| Code | Name ✓ | Value meaning | Njord label | Evidence |
|------|--------|---------------|-------------|----------|
| 4 | timer set | countdown length, seconds (always 300 = the 5:00 default) | — | Always immediately precedes code 0 at the same ms |
| 0 | timer reset / re-arm | countdown length, seconds (300) | `Vakaros Timer Reset` | The 4+0 pair matches Njord "Vakaros Timer Reset" comments (e.g. 17:47:30.512 vs Njord 17:47:30.511). Logged when the timer is reset to the default 5:00 after a race |
| 1 | timer start / sync | **seconds remaining to the gun**, rounded up | `Vakaros Timer Start (N seconds)` | Gun = timestamp + value, rounded down to the whole second: e.g. 19:16:09.829 + 831 s → gun 19:30:00. Verified for all five races across both files. Values are not round because the countdown is synced to the committee's scheduled start |
| 3 | timer expired (the gun) | 0 | `Gun` | Fires at the exact gun time of every race (19:55:00.000, 21:05:00.000, 22:15:00.000, 19:30:00.001, 21:00:00.000), matching Njord's `Gun` events to the millisecond |

The same timer events appear in multiple boats' logs with identical values within ~1 s of each other, so they are fleet-wide race-committee broadcasts (RaceSense-style), logged by each unit on receipt — not local button presses. Njord also shows a per-boat `Vakaros Timer Synced (N seconds)` label; whether that maps to code 1 or a yet-unseen code cannot be determined from these files.

### `0x05` — Start-line end position (17 bytes)

Live broadcast positions of the two ends of the start line (committee boat and pin), presumably received over the Vakaros line-broadcast mechanism. Logged at roughly 1–2 Hz per end while a line is set.

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u64` | timestamp | ms since epoch, UTC |
| 8 | `u8` | end id ✓ | `0` or `1`. Validated against Njord course geometry: end 0 matches the first `StartLine` coordinate and end 1 the second, both within ~0.5 m of Njord's values |
| 9 | `f32` | latitude ✓ | degrees (plain float — note the lower precision vs. the `i32 × 10⁻⁷` used in `0x02`: ~1 m at mid-latitudes) |
| 13 | `f32` | longitude ✓ | degrees |

### `0x07` — Periodic status B (12 bytes) ⚠

Logged once per minute, paired with `0x03`.

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u64` | timestamp | ms since epoch, UTC |
| 8 | `u32` | battery ✓ | Byte 1 (bits 8–15) is the **battery charge percentage**: drains 100 → 97 over the 8 h 9-26 session, and the 9-27 session starts at 97 and drains to 89. Bytes 0, 2, 3 were always zero; the field may be a fixed-point percentage (value / 256) |

### `0x08` — One-shot status (13 bytes) ⚠

A single record at the very start of the log.

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u64` | timestamp | ms since epoch, UTC |
| 8 | 5 × `u8` | unknown ⚠ | `01 00 00 00 02` observed. Could be a config/version blob (e.g. `u32` = 1 + `u8` = 2) |

### `0x0E` — Session marker (16 bytes) ⚠

Two records at the start of the log and one at the very end.

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | `u64` | timestamp | ms since epoch, UTC |
| 8 | 8 × `u8` | unknown ⚠ | All `0xFF` in every instance. Placement suggests session open/close markers with a null payload (perhaps a slot for an ID that was unset) |

---

## 4. Parsing algorithm

1. Read the whole file (or stream page by page).
2. At each offset: read one key byte, look up its payload size, slice the payload, advance. **Abort on unknown keys** — do not attempt to resynchronize, as fixed-size framing gives no reliable resync point.
3. `0xFF`/`0xFE` can be skipped entirely (or used to validate page integrity via the footer's length field).
4. Decode payloads per §3.

A validation trick that proved useful while reverse-engineering: every data record starts with a u64 millisecond timestamp, so candidate record boundaries can be verified by checking that the 8 bytes after a key byte decode to a plausible epoch-ms value close in time to its neighbors.

## 5. Known limitations of this spec

- Derived from **two files, one device, one firmware**. Record sizes could differ across firmware versions, and record types not present in the source sessions (wind, speed-through-water, heart rate, etc.) are entirely undocumented here.
- Fields marked ⚠ are structural placeholders: their sizes are certain (both files parse cleanly end to end), their meanings are not. Fields marked ✓ were validated against Njord Analytics exports of the same races.
- The page footer is confirmed to be a length field (page lengths vary and the footer tracks them), but it over-states the length by exactly 53 bytes on a few pages containing `0x04` records — use the key-driven record sizes for framing, not the footer.
