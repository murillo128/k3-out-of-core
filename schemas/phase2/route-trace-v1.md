# K3 route trace schema version 1

All integers and IEEE-754 floats use little-endian byte order. The stream consists of a header, zero or more length-framed records, and a fixed completion trailer.

## Header

```text
8 bytes   magic: K3ROUTE\0
u32       schema version: 1
u32       header payload length
bytes     header payload
```

The header payload contains, in order:

```text
string    exact GGUF file name (`u32` length + UTF-8 bytes)
u64       exact GGUF file size in bytes
string    exact GGUF SHA-256
string    exact model source revision
string    exact published GGUF revision
string    exact `llama.cpp` revision
string    trace-local run ID
u32       expert count
u32       top-k
u32       routed-layer count
```

## Record frame

```text
u32       magic: 0x44434552 (RECD)
u32       payload length
u64       record ordinal
u64       request ordinal
u64       ubatch ordinal
u32       phase: 1 PREFILL, 2 DECODE
i32       layer
u32       batch row
i32       logical position
u32       sequence-ID count
i32[]     sequence IDs
u32       top-k
i32[]     selected expert IDs in consumed rank order
f32[]     corresponding final consumed routing weights
```

Records are ordered by request ordinal, ubatch ordinal, layer, and batch row. Rank order is the array order within each record.

## Completion trailer

```text
8 bytes   magic: K3DONE\0\0
u64       record count
u32       CRC-32 of every byte before the trailer
u32       reserved, must be zero
```

A stream without the complete trailer, with an unsupported version, an impossible length/count, a non-contiguous record ordinal, non-canonical ordering, an out-of-range expert, a non-finite weight, or a checksum mismatch is incomplete or corrupt and must be rejected.
