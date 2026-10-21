"""Full-Duplex-Bench adapter (not built yet; the full benchmark harness is out of scope for the first spike).

Plan, per docs/architecture.md "Full-Duplex-Bench adapter":
* stream the v1/v1.5 user-channel wav through the pipeline in real time
  (harness.file_input.WavFileSource already does the pacing);
* record every output audio frame with its wall-clock offset into a same-length wav;
* run the Full-Duplex-Bench evaluation scripts on the pair.

Full-Duplex-Bench is CC BY-NC 4.0: coursework use only, no commercial claims without permission.
"""


def run(*_args, **_kwargs):
    raise NotImplementedError("fdb_adapter is scheduled after Gate 1; see docs/architecture.md")
