# Latest-only MJPEG decoding design

## Intent

Reduce PS CPU spent JPEG-decoding camera frames that the processing loop will overwrite before using. The integrated camera path requests MJPG and Gray8 decode, while `LatestCamera` keeps only the newest published frame.

## Goals

- Keep continuous camera capture and the current latest-frame consumer contract.
- Retain compressed MJPEG frames in the capture thread and decode only a frame selected by `read(after=...)`.
- Preserve monotonically increasing capture sequence numbers, capture-time timestamps, timeout behavior, and explicit camera/decode errors.
- Preserve the original `read()` deadline across synchronous decode: if decode completes after that deadline, cache the decoded frame but raise a `TimeoutError` subtype carrying that frame; the integrated runner consumes the carried frame, while a later read may also return the cache immediately. A genuine no-fresh-frame `TimeoutError` continues through camera recovery.
- Reuse the most recently decoded Gray8 frame if a caller reads again before a newer raw packet arrives.
- Keep the direct camera-entrypoint behavior available where it is used outside `LatestCamera`.

## Non-goals

- Change the requested camera FPS or V4L2 mode.
- Change RLE, counting, display, or PL interfaces.
- Claim a CPU or FPS improvement without a comparable board measurement.

## Approaches considered

1. **Latest-only on-demand decode — selected.** The capture producer continually reads frames and retains one newest compressed packet. A consumer read snapshots that packet and decodes it once. It avoids work for packets replaced before consumption and requires no fixed sampling-rate choice.
2. **Fixed decode-rate cap.** Decode at a configured maximum while capture continues. This keeps decoding off the consumer thread but introduces a chosen sampling limit that could miss fast-moving material.
3. **Lower camera capture FPS.** Reduce camera transport and decode work at the source, but the negotiated rate/control behavior is not currently recorded and fewer captured frames may affect counting.

## Design

Add a raw-MJPEG camera iterator for the integrated `LatestCamera` path. Keep the existing decoded-frame iterator as a compatibility wrapper for the standalone camera entrypoint. The capture thread records each raw packet with its capture sequence and monotonic capture timestamp, replacing the prior packet. `LatestCamera.read(after=...)` waits for a newer sequence, snapshots the newest raw packet and metadata under the condition lock, and decodes outside the lock. Cache the last decoded `(sequence, gray8, timestamp)` so repeated reads can reuse it without another JPEG decode. Keep the deadline established at method entry: if synchronous decoding finishes after the deadline, retain the cache, clear the in-flight state, notify waiters, and raise a `TimeoutError` subtype carrying the completed tuple. The integrated runner unwraps this particular timeout into a frame result, so the frame is processed without camera recovery; an ordinary no-fresh-frame `TimeoutError` remains on the recovery path. A subsequent direct read may also return the cached tuple immediately. Return the existing tuple shape. The existing JPEG SOI and 640×400 Gray8 checks remain in force. Malformed data and capture failures remain observable to readers; close continues to join the capture thread and release V4L2.

## Constraints and risks

- The decoder moves into the consumer's calling thread. This lowers total decode count, but removes decode/processing overlap and may increase per-frame critical-path latency. Compare CPU and processing FPS before describing the net result as a performance improvement.
- Only the Python JPEG decode is avoided for replaced frames; camera reads, USB delivery, and kernel interrupt work remain.
- Frame sequences must count captured packets yielded after warmup, including packets overwritten before a read. Timestamps must be taken at capture, not after decode, because count association uses elapsed time.
- Keep memory bounded to the latest compressed packet, the last decoded Gray8 frame, and any packet currently held by an active read during decode.

## Acceptance evidence

- Tests prove that when multiple raw frames arrive before a consumer read, the latest capture sequence is returned and only that selected packet is decoded; subsequent reads reuse the cached decode until capture advances.
- The newest-frame test synchronizes on producer completion of all three packets before calling `read()`.
- A regression test forces decode past the read deadline and verifies that the call times out, the completed frame remains cached, and retry does not decode again. Already-cached reads remain immediate.
- The integrated camera boundary consumes the frame carried by that timeout without closing/restarting the camera, while a genuine no-frame timeout remains a recovery-triggering timeout.
- Decoder control-flow `BaseException` values propagate unchanged after in-flight state is cleared; no-packet exhaustion/idle timeout and concurrent read decode deduplication are covered.
- Tests preserve decoded pixels, capture sequence/timestamp behavior, timeout, capture/decode error, and close behavior.
- Run the focused camera and entrypoint tests. Board CPU and processing-FPS comparison remains a separate runtime acceptance check.
