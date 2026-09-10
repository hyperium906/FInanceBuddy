"""Offline checks for optional voice input.

Asserts the module behaves whether or not its optional dependencies are
installed, and that nothing in it raises. Transcription of a real clip is
exercised only when faster-whisper is present.

Run: python tests/test_voice.py
"""
import sys, pathlib, subprocess, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from finance_app import voice

print("=== availability reports honestly ===")
a = voice.availability()
print(f"  recorder={a.recorder} transcriber={a.transcriber} usable={a.usable}")
assert isinstance(a.usable, bool)
assert a.usable == (a.recorder and a.transcriber)

print("\n=== the message names exactly what is missing ===")
for rec, trans, expect in [
    (False, False, ["audio-recorder-streamlit", "faster-whisper"]),
    (False, True,  ["audio-recorder-streamlit"]),
    (True,  False, ["faster-whisper"]),
    (True,  True,  []),
]:
    msg = voice.Availability(rec, trans).message
    for name in expect:
        assert name in msg, (rec, trans, msg)
    if not expect:
        assert msg == ""
    print(f"  recorder={str(rec):5} transcriber={str(trans):5} -> "
          f"{(msg[:58] + '…') if msg else '(no message needed)'}")
assert "Typing works either way" in voice.Availability(False, False).message
print("  always tells the user typing still works")

print("\n=== nothing raises, whatever the input ===")
for bad in [None, b"", b"tiny", b"x" * 5000]:
    result = voice.transcribe(bad)
    label = "None" if bad is None else f"{len(bad)} bytes"
    print(f"  {label:12} -> ok={result.ok} reason={result.reason[:52]}")
    assert result.ok is False and result.reason
    assert isinstance(result.text, str)
print("  every failure is a Transcription, never an exception")

print("\n=== record() is safe when the component is absent ===")
if not a.recorder:
    assert voice.record() is None
    print("  returns None, no crash")
else:
    print("  (component installed; widget needs a Streamlit runtime — skipped)")

print("\n=== model settings are configurable ===")
print(f"  WHISPER_MODEL={voice.WHISPER_MODEL!r} "
      f"WHISPER_COMPUTE_TYPE={voice.WHISPER_COMPUTE_TYPE!r}")
assert voice.WHISPER_MODEL and voice.WHISPER_COMPUTE_TYPE
assert voice.MIN_CLIP_BYTES > 0

if a.transcriber:
    print("\n=== real speech round-trip ===")
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = f"{tmp}/s.aiff", f"{tmp}/s.wav"
        made = subprocess.run(["say", "-o", aiff, "buy a standing desk"],
                              capture_output=True).returncode == 0
        if made and subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav],
            capture_output=True).returncode == 0:
            result = voice.transcribe(pathlib.Path(wav).read_bytes())
            print(f"  ok={result.ok} lang={result.language} text={result.text!r}")
            assert result.ok and "desk" in result.text.lower()
            print("  transcribed locally — no audio left the machine")
        else:
            print("  (no `say`/`afconvert` on this platform — skipped)")
else:
    print("\n  (faster-whisper not installed — transcription round-trip skipped)")

print("\nALL ASSERTIONS PASSED")
