# ME3 Research: Full-Duplex Voice Agent on Raspberry Pi 5

Revision 2, Oct 21, 2026 · Quiel Quiwa. Updated after a verification pass; corrections are listed at the end of the Summary.

## Summary

The research points to a cascaded, streaming voice agent: a thin Raspberry Pi 5 client that runs VAD and audio I/O, commercial streaming ASR and TTS, a fast and cheap LLM, and an MCP tool layer, at roughly $1.10 to $1.61 per hour of conversation at list prices (about $0.8 to $1.3 if the agent talks less). Six findings drive that.

- **Native full-duplex models are the quality bar, not the build target.** Moshi, PersonaPlex, and NemotronLabs VoiceChat 11B converse most naturally, but Nemotron needs at least 80 GB of GPU memory, reaches only 33% pass@1 on tool calls in NVIDIA's own spoken benchmark, and ships research-only weights (third-party figures, not reproduced). A cascaded stack keeps text-LLM tool reliability and any MCP tool.
- **Only VAD and turn detection need to live on the Pi.** Silero VAD (MIT) and Pipecat's Smart Turn v3 (BSD-2) are the candidates. TEN VAD is dropped because its licence bars competing with Agora's offerings. Smart Turn publishes 12.6 to 94.8 ms on cloud CPUs and 59.8 ms on a one-vCPU ARM instance, but no Raspberry Pi 5 figure, so the first Pi task is to measure it.
- **The harness question has two layers, and Pipecat 1.x is the choice.** Onit (Apache-2.0, MCP-capable, text UI) and baby-onit (no MCP) are tool-calling loops. Pipecat and LiveKit Agents are voice runtimes with MCP loading and interruption handling. Pipecat's `MCPClient` is confirmed in its reference docs (stdio, SSE, and streamable HTTP), but it has no documented hook for server notifications. No source I read gives onit's own latency, so measure harness overhead against a stub model before blaming the harness; the DGX endpoint may be the slow part.
- **ASR and TTS are the cost, not the LLM.** In the cheapest cascaded stack, TTS is about half the bill, ASR about 40%, and the LLM about 8%. The big hidden cost is a bloated harness prompt, which can multiply the LLM line several times over.
- **Benchmarking can start from Full-Duplex-Bench**, one repository with v1/v1.5, v2, and v3 folders under CC BY-NC 4.0. v1 and v1.5 score pause handling, interruption, backchannels, side talk, and background speech; v3 adds tool use with disfluency. ME3 still needs its own task-success and cost-per-successful-task measures and a scripted-audio test set, and no standard cost-per-task voice benchmark exists.
- **Echo, notification plumbing, and licences are the main surprises.** Pipecat has no built-in echo cancellation, so the speakerphone choice matters; the MCP hook for events needs a small bridge we write; and Spotify, TEN VAD, and the benchmark licence limit what could be sold.

One design tension was decided in the architecture review (MCP carries control only; audio flows on a local side channel): the spec wants ASR and TTS as MCP servers we build, but MCP tools are request and response while barge-in needs a stream that can be cancelled within tens of milliseconds.

### What changed in this revision

1. Pipecat's old `MinWordsInterruptionStrategy` and `allow_interruptions` were removed in 1.0. Pipecat 1.x uses `user_turn_strategies` instead (see the turn-taking section).
2. Silero's defaults declare speech after about 192 ms, so the duck target is under 100 ms with tuned parameters, not under 50 ms.
3. Pipecat has no echo cancellation. A hardware-echo-cancelling speakerphone is the first option, then PipeWire's WebRTC module.
4. TEN VAD is out because of its licence.
5. Smart Turn v3 has a published false "complete" rate of 3.75% for English, so an incomplete-turn fallback is needed.
6. Full-Duplex-Bench is a single repository with three version folders under CC BY-NC 4.0; v3 covers tool use.
7. The LLM shortlist is Groq gpt-oss-20b and 120b, Claude Haiku 5.5, and Gemini 3.1 Flash-Lite. Gemini 3.5 Flash-Lite is off the live path (5 to 8 s to first token with thinking).
8. The budget is rebuilt to about $20 cash, with the Deepgram credit covering development.
9. Cartesia Pro's 100K credits cover only about 2 hours of agent speech, and word timestamps (needed to truncate context to what was heard) require Cartesia, ElevenLabs, or Rime, not Deepgram Aura.
10. Spotify is demo only.
11. Licensing is now covered in its own section below.
12. Hardware cost of a sellable unit is about $300.

## Requirements and constraints

ME3 asks for a generic, hands-free, interruptible voice agent on a Raspberry Pi 5, where only VAD (and the agent harness) runs on the device and everything heavy runs remotely.

| Requirement | Source | Implication |
| --- | --- | --- |
| Agent harness with MCP support on the RPi | ME3 spec | Harness runs on device; tools are MCP servers we build |
| MCP servers for ASR and TTS that we build and run | ME3 spec | ASR and TTS are exposed as MCP tools ("listen", "speak"), even if the model behind them is a commercial API |
| VAD on the RPi | ME3 spec, class notes 12 and 17 | Only local model; must be lightweight and always on |
| Full duplex: user can barge in, agent stops and listens | Class notes 4, 5, 16 | Playback must be cancellable mid-utterance; VAD must keep running while the agent speaks |
| No wake word | Class note 6 | Needs a policy for when the agent is being addressed (open design question) |
| English only | Class note 9 | Simplifies ASR and TTS choice |
| 10 base tasks from ME2, plus one random complex task at evaluation | Class notes 10 and 18 | Tool set must be scalable; one complex task (for example building a website) must fit the harness |
| Remote, reliable operation | Class note 8 | Network resilience, reconnects, and graceful degradation matter |
| Latency and naturalness are the main criteria | Class note 15 | Benchmark is a deliverable, not an afterthought |
| Commercial APIs allowed | Class note 13 | Cost per conversation minute becomes a design axis |

The 10 ME2 base tasks (from the VCM exercise): play music, ask a question or search, control lights, dim or color lights, set a timer, set an alarm, adjust thermostat, media control, reminders and lists, calls and messaging.

### Evaluation expectations

- Real-time interaction quality: turn-taking, interruption handling, and response latency, starting from the Full-Duplex-Bench metrics the class agreed to use.
- Content quality and instruction following: separate benchmarks, as noted in the class chat.
- Tool-calling success on the 10 base tasks and the random complex task. Neither Full-Duplex-Bench paper measures this, so we define it ourselves.

## Voice agent paradigms

There are three ways to build a voice agent, and only the cascaded one fits our spec cleanly: it is the only one where a general harness with MCP tools does the thinking and where the RPi needs nothing heavier than VAD.

| Paradigm | How it works | Examples | Fit for ME3 |
| --- | --- | --- | --- |
| Cascaded (STT, LLM, TTS) | Streaming ASR feeds a text LLM, whose tokens stream into TTS. VAD and turn detection sit outside the models. | Outside course slides (LiveKit, Deepgram, ElevenLabs), not Prof's lectures; Freeze-Omni is the cascaded reference in Full-Duplex-Bench | Best fit. Any harness and any MCP tool plug in. Latency is the sum of stages (outside course slides: about 540 ms best, 1.6 s worst). |
| Speech-to-speech API (half-duplex or "realtime") | One hosted model takes audio in and returns audio out, with function calling. | OpenAI Realtime, Gemini Live, Azure OpenAI Realtime (all named on the outside course slides) | Good fallback for latency. Tools are limited to what the API supports, and per-minute cost is higher. |
| Native full-duplex model | One model listens and speaks at once, deciding every few milliseconds whether to talk. | Moshi, PersonaPlex, Nemotron VoiceChat | Best conversational feel, weakest tool use. Needs a GPU server, so it would sit beside the DGX, not the RPi. NVIDIA's VoiceChat weights are research-only. |

### What the native full-duplex models offer and cost

- **Moshi (Kyutai):** the base for the other two. Kyutai's April 2026 post, titled "MoshiRAG: Asynchronous Knowledge Retrieval for Full-Duplex Speech Language Models," shows retrieval being added to a full-duplex model, which signals that tool use for this family is still being researched. I could not read the post body, so details are unverified. Jus reported in the class chat that Moshiko did not call tools.
- **[PersonaPlex](https://research.nvidia.com/labs/adlr/personaplex/) (NVIDIA):** a 7B dual-stream model built on Moshi, trained on 1,217 hours of real Fisher conversations plus about 2,250 hours of synthetic service conversations. It does backchanneling. NVIDIA's chart shows about 0.2 s average latency versus roughly 1 to 1.4 s for others, which the page itself says is approximate. Code is MIT; weights use the NVIDIA Open Model License. It has no tool-calling story that I could find.
- **[NemotronLabs VoiceChat 11B](https://aiweekly.co/alerts/nvidia-opens-nemotronlabs-voicechat-11b-with-tool-calling) (NVIDIA, released Aug 3, 2026):** the first open full-duplex model with function calling, per press coverage. NVIDIA reports about 450 ms smooth turn-taking and 480 ms to yield on interruption. Third-party analysis ([OrcaRouter](https://www.orcarouter.ai/blog/nvidia-nemotron-voicechat-11b-explained), a commercial vendor) lists the limits that matter to us:
  - at least 80 GB of GPU memory, tested on H100 with vLLM
  - tool calling at 33% pass@1 on Full-Duplex-Bench v3, with argument accuracy at 44.2%
  - up to five tools per session, no user interruption during tool execution, ASCII-only prompts and tool results
  - English only, about two minutes of audio context, no reliable backchanneling, quiet environments only
  - weights are research-only; inference code is Apache-2.0

The class chat mentions a "talker-thinker" setup: a fast conversational layer speaks while a separate agent does the work. That hybrid is what the outside course slides' agent-plus-voice split and Jus's asynchronous agent workload point both lead to. It keeps the tool reliability of a text LLM and the interruptibility of a streaming voice layer.

None of these model claims were reproduced by us. The Nemotron numbers in particular come from NVIDIA and a vendor blog.

## Turn-taking, VAD, and barge-in

A cascaded agent feels natural only if three small components behave: VAD (is someone speaking), end-of-turn detection (are they done), and an interrupt handler (stop talking now). The first runs on the RPi; the second and third can too, if they stay on CPU.

### How the outside course slides describe it

- **Turn detection** combines a binary VAD classifier with a transformer that reads the transcript and answers "end of utterance, yes or no?" If either says the user is not done, a delay timer is set before the end-of-turn event fires.
- **Interruption handling** adds an interrupt handler watching user speech while TTS plays. If the user interrupts, agent playback stops and the pipeline returns to listening.
- **Latency budget** (LiveKit figures on the outside course slides): VAD 20 ms, end-of-utterance 100 ms, ASR 100 to 500 ms, LLM time to first token 220 to 500 ms, TTS time to first byte 100 to 450 ms. Humans expect a reply in about 236 ms on average.

### Pipecat 1.x turn-taking, as verified

- **Version.** Pipecat reached 1.0.0 on Apr 14, 2026; 1.13.0 was current on Oct 19, 2026. The old interruption strategies (`MinWordsInterruptionStrategy`, deprecated in 0.0.99) and the `allow_interruptions` argument no longer exist, so examples written before 1.0 will fail or be silently ignored.
- **User turn strategies.** Interruptions are configured with `user_turn_strategies`: start strategies such as `VADUserTurnStartStrategy`, `TranscriptionUserTurnStartStrategy`, and `MinWordsUserTurnStartStrategy`, and stop strategies such as `LocalSmartTurnAnalyzerV3`. The default pairs VAD plus transcription for start and Smart Turn v3 for stop.
- **What an interruption does.** An `InterruptionFrame` stops the LLM and TTS and drops queued output audio. There is no built-in duck or pause mode, so a duck-then-decide behavior (lower the volume while deciding whether it is a real interruption) is custom code in a processor before the transport output.
- **Silero defaults.** Confidence 0.7, `start_secs` 0.2, `stop_secs` 0.2, on 32 ms windows, so speech is declared after about 192 ms. Tuning `start_secs` to roughly 0.064 to 0.1 trades speed against false starts.
- **Truncating context to what was heard.** Pipecat does this with word timestamps from the TTS service. Cartesia, ElevenLabs (WebSocket), and Rime provide them; Deepgram Aura does not, and Deepgram's Flux TTS has an open issue about it. Keep sentence-level aggregation, since token-level aggregation has a known bug.
- **Smart Turn v3.** 8M parameters, about 8 MB in int8, 23 languages, BSD-2. Published CPU inference is 12.6 to 94.8 ms across cloud CPUs and 59.8 ms on a one-vCPU ARM instance. No Raspberry Pi 5 figure is published; my estimate is 60 to 120 ms, to be measured. The published false "complete" rate is 4.73% overall and 3.75% for English.

### Options for the local pieces

| Component | Option | What we know | Open question on RPi 5 |
| --- | --- | --- | --- |
| VAD | Silero VAD | MIT. Used in the outside course's LiveKit example (`silero.VAD.load()`) and the default in Pipecat. About 2.2 MB model ([TEN VAD page](https://huggingface.co/TEN-framework/ten-vad)). | Real-time factor on the Pi 5; the only published Silero comparison is on a Xeon server |
| VAD | TEN VAD (not used) | Apache-2.0 plus a clause against deploying it in a way that competes with Agora's offerings, so it is excluded from a product path. About 306 KB on Linux, real-time factor 0.0086 versus Silero 0.0127 on one Xeon, 10 or 16 ms hops. The page claims faster speech-to-silence detection than Silero. Apache-2.0 with extra conditions. | The page lists Linux x64 but does not mention ARM Linux or Raspberry Pi, so a Pi 5 build is unconfirmed |
| Turn detection | Pipecat Smart Turn v3 | [Open-source ONNX model](https://docs.pipecat.ai/server/utilities/turn-detection/smart-turn-overview) that reads up to the last 8 seconds of audio and labels the turn complete or incomplete. Designed for CPU, BSD-2, 23 languages, short VAD `stop_secs` of 0.2 recommended. Published inference is 12.6 to 94.8 ms on cloud CPUs. | Inference time on a Pi 5 core; the docs do not mention Raspberry Pi |
| Turn detection | Transcript-based end-of-utterance model | Reads ASR text, as in the outside course slides' semantic turn detection | Adds a dependency on streaming transcripts arriving fast enough |

### Barge-in and echo

Barge-in only works if the agent does not hear itself. If the mic picks up the speaker, VAD fires on the agent's own voice and the agent interrupts itself. There are two common remedies, both listed as design knowledge rather than sourced findings:

Pipecat has no echo cancellation of its own (a long-open feature request), so this is solved outside it, in this order:

1. Use a USB speakerphone with hardware echo cancellation. The VCM plan picked an Anker PowerConf S3 class device; a Jabra Speak2 40 (about $165) is another candidate. Which one actually cancels well enough at normal volume is untested.
2. Add software echo cancellation with PipeWire's echo-cancel module and the WebRTC backend between mic and speaker. This costs CPU on the Pi and needs tuning.
3. As a last resort, an RMS gate that raises the VAD threshold while the agent is speaking. It makes quiet interruptions harder.

Stopping must also be fast and clean. On barge-in the agent needs to (a) stop audio playback within one audio buffer, (b) cancel the in-flight TTS and LLM streams, and (c) record how much of its reply the user actually heard, so the next turn is coherent. Full-Duplex-Bench v1 scores the agent's stop and recover behavior, and v1.5 adds overlapping speech cases such as backchannels, talking to others, and background speech (see the benchmarks section).

## ASR and TTS options

Streaming APIs from Deepgram, Cartesia, and ElevenLabs are the practical default for a remote, reliable demo; Qwen3 models on the DGX are the open option, but DGX queueing is the class's known bottleneck. Interruptibility in a cascaded stack is mostly a playback property, not a model property: any streaming TTS can be cut by stopping playback and closing its stream, provided the client keeps only a short audio buffer.

### Speech recognition (streaming)

| Option | Price | Notes | Source |
| --- | --- | --- | --- |
| Deepgram Nova-3 (English) | $0.0048 per min promo, $0.0077 regular | Cheapest streaming option listed; $200 free credit for new accounts | [Deepgram pricing](https://deepgram.com/pricing) |
| Deepgram Flux (English) | $0.0065 per min promo, $0.0077 regular | Streaming model with built-in end-of-turn signals, including eager end-of-turn; an alternative to running Smart Turn on the Pi, to be A/B tested. 24 hours of streamed audio would cost about $9.36 per device per day, so send ASR only the speech | [Deepgram pricing](https://deepgram.com/pricing) |
| Cartesia Ink | $0.39 per hour (Scale plan), 3 credits per second | Free tier gives about 1h 51m of STT per month | [Cartesia pricing](https://cartesia.ai/pricing) |
| ElevenLabs Scribe v2 Realtime | $0.39 per hour | Vendor-stated latency of about 150 ms | [ElevenLabs API pricing](https://elevenlabs.io/pricing/api) |
| Groq Whisper Large V3 Turbo | $0.04 per hour | Not a streaming model; LiveKit's docs note such models need VAD buffering until the end of an utterance, which adds delay | [Groq models](https://console.groq.com/docs/models), [LiveKit STT docs](https://docs.livekit.io/agents/models/stt/) |
| Qwen3-ASR 1.7B (open, Apache-2.0) | Self-hosted GPU cost | Baseten reports real-time streaming at up to 64 concurrent streams with P95 finalization under 0.50 s on RTX 6000 or H100 | [Baseten](https://www.baseten.co/library/qwen3-asr-1-7b/) |

Anthony's point in the class chat is the right priority: a mispronounced reply is forgiven, a mistranscribed command is not, because the error flows into the LLM and the tool call. ASR accuracy on names, numbers, and dates (the credit-card birthday example) deserves its own test set.

### Speech synthesis (streaming)

| Option | Price | Notes | Source |
| --- | --- | --- | --- |
| Cartesia Sonic | About $37 to $50 per 1M characters, 750 to 800 characters per audio minute | Roughly $0.03 to $0.04 per audio minute. Pro is $5 per month with 100K credits and commercial use (about 2 hours of agent speech); overage about $65 per 1M characters (third-party). The free tier is personal use only. Provides word timestamps | [Cartesia pricing](https://cartesia.ai/pricing) |
| Deepgram Aura-2 | $0.030 per 1,000 characters | About $0.023 per audio minute at 775 characters per minute. No word timestamps, so context cannot be truncated to what was heard. Flux TTS is $0.045 per 1,000 characters and has the same gap | [Deepgram pricing](https://deepgram.com/pricing) |
| ElevenLabs Flash / Turbo v2.5 | $0.04 per 1,000 characters | Vendor-stated latency of about 75 ms; the free tier is non-commercial. Word timestamps over WebSocket | [ElevenLabs API pricing](https://elevenlabs.io/pricing/api) |
| Groq Orpheus V1 English (preview) | $22 per 1M characters | About $0.017 per audio minute; preview models may be withdrawn at short notice | [Groq models](https://console.groq.com/docs/models) |
| Qwen3-TTS 0.6B and 1.7B (open, Apache-2.0) | Self-hosted GPU cost | 97 to 101 ms first-packet latency at concurrency 1 for the 12Hz models; streaming text in, audio out; packets cover about 320 ms of speech. The report does not describe barge-in. | [Qwen3-TTS paper](https://arxiv.org/html/2601.15621) |

For TTS, time to first audio byte and the size of the playback buffer matter more than raw quality, since both set how quickly the agent starts and how quickly it can stop. Kokoro and Piper appear in Pipecat's supported TTS list as local options, but the spec keeps models off the RPi, so they are out of scope except as a failure fallback.

The comparisons above use vendor-listed prices (some via third-party aggregators, and some promotional rates that may end) as fetched on Oct 21, 2026. Latency figures are vendor claims until we measure them from the Pi.

## LLM options and cost

For voice, the LLM's price matters less than its time to first token and its tool-calling reliability, because the LLM cost per conversation minute is already a fraction of a cent at the cheap end. The outside course slides put LLM time to first token at 220 to 500 ms, the largest single block in the latency budget after ASR.

| Model | Input per 1M tokens | Output per 1M tokens | Status |
| --- | --- | --- | --- |
| [Groq gpt-oss-20b](https://console.groq.com/docs/models) | $0.075 | $0.30 | Shortlist 1. About 1,000 tokens per second per Groq. Free tier 30 requests per minute, 1,000 per day |
| [Groq gpt-oss-120b](https://console.groq.com/docs/models) | $0.15 | $0.60 | Shortlist 2. About 500 tokens per second; median time to first token 234 ms on OpenRouter (third-party; another source reports 0.71 s with reasoning) |
| [Claude Haiku 5.5](https://platform.claude.com/docs/en/about-claude/pricing) | $0.10 | $0.50 | Shortlist 3. Released Oct 7, 2026. Cache reads $0.01 per 1M; prices rise above 100K-token prompts |
| Gemini 3.1 Flash-Lite ([Google](https://ai.google.dev/gemini-api/docs/pricing)) | $0.25 | $1.50 | Shortlist 4, with thinking off |
| Gemini 3.5 Flash-Lite | $0.30 | $2.50 | Off the live path: Artificial Analysis measures 7.75 s to first token with thinking (a hands-on test reports 1.2 s) |
| GPT-5 mini ([BenchLM](https://benchlm.ai/llm-pricing)) | $0.25 | $2.00 | Aggregator-reported; not yet benchmarked |
| Mercury 3, DeepSeek V4.1 Flash, GLM 5.3 Flash, MiniMax M3.1 Flash | Listed at about $0.07 to $0.30 | About $0.35 to $1.20 | On OpenRouter or Ollama Cloud; prices and speeds unverified, not yet benchmarked |
| Qwen3.7 Flash ([BenchLM](https://benchlm.ai/llm-pricing)) | $0.03 | $0.13 | Aggregator-reported; not yet benchmarked |

Groq's Llama 3.1 8B and 3.3 70B were reported deprecated on Aug 16, 2026, and Gemini 3.8 Flash ($0.75 and $3.75, doubling from Jan 1, 2027) is too expensive for the live path.

Prices as of Oct 20 to 21, 2026. Two things the table does not settle:

- **Tool-calling quality at the cheap end.** Cheap fast models are the ones most likely to fumble tool arguments. We have no measured numbers for these models, so the benchmark must include our own tool-call success test on the 10 base tasks.
- **Context size per call.** An agent harness sends its system prompt, tool schemas, and history on every call. One review reports that a full-featured harness (Hermes Agent) spends about 73% of each call on fixed overhead, reaching 15,000 to 20,000 input tokens per request through messaging gateways. Voice turns are frequent and short, so a lean prompt and prompt caching are the two biggest LLM cost levers.

The class chat mentions running Qwen on the DGX. That removes the per-token bill but adds queueing delay when many students share the cluster, and it moves time to first token outside our control. A sensible pattern is a fast cheap model for conversation and a stronger model reserved for the one complex task at evaluation, such as building a website.

## Agent harness landscape

Onit is a capable tool-calling harness with MCP, but it is built for text, and nothing I could read about it measures or promises low latency. Before replacing it, separate the harness's own overhead from the slow DGX model endpoint it is deployed against, because the two are easy to confuse.

| Harness | License | MCP | What it is | Fit for a voice agent |
| --- | --- | --- | --- | --- |
| [onit](https://github.com/sibyl-oracles/onit) | Apache-2.0 | About 10 stars; setup takes an endpoint, key, and model, and there is no programmatic API, so it is wrapped as a subprocess behind a `delegate` tool. Has a large `mcp` module, a FastMCP prompt layer, built-in tools, web, bash and filesystem servers, and external MCP servers over SSE | Terminal UI and FastAPI web UI with SSE. OpenAI-compatible endpoints with ordered failover. Python 3.10 to 3.12. No voice interface listed. | Good tool plumbing. The prompt-engineering layer and tool registry add per-request overhead that nobody has measured. Needs a voice front end and a streaming path. |
| [baby-onit](https://github.com/sibyl-oracles/baby-onit) | Apache-2.0 | None, by its own README | About 1,000 lines in one file. Synchronous "model proposes, harness acts" loop. Tools are in-process Python functions. | Best for learning and for measuring a lean loop. Needs an MCP client added to meet the spec. |
| [Hermes Agent](https://www.fast.io/resources/hermes-agent-review-2026.md) (Nous Research) | MIT | MCP client, and can expose itself as an MCP server | Self-hosted runtime with memory, cron, 20+ messaging platforms, 70+ tools, and a voice push-to-talk mode in its CLI. | Jus already used it in the class chat. A review reports heavy fixed token overhead per call (15,000 to 20,000 input tokens through messaging gateways), which hurts voice latency and cost. |
| LangGraph, CrewAI | Open source | CrewAI has announced MCP integration | General orchestration frameworks, described in a [vendor comparison](https://www.edenai.co/post/best-ai-agent-harnesses-comparison-guide) | CrewAI has no audio layer and is not for the voice path. Possible later as a backend behind `delegate`. |

The Agents deck's rule applies here: start with the simplest pattern that works and escalate only when needed. A voice turn is short and latency-bound, so the harness should be a thin loop with a small prompt, parallel read-only tool calls, and streamed output, which is how baby-onit's loop is designed.

### Where the time goes in a harness

I could not find any published latency figure for onit, so this is a measurement task, not a research finding. Three costs are separable and should each be timed:

1. Harness overhead: prompt assembly, tool-schema serialization, and MCP round trips, measured against a stub LLM that answers instantly.
2. Model time to first token: measured against each candidate endpoint (the DGX cluster, and one or two commercial APIs) with the same prompt.
3. Tool execution time: per MCP server, including the network hop from the Pi.

That split also matches Prof's Efficiency deck method: warm up, time inside a loop, and report percentiles rather than a single run.

## Voice frameworks

Pipecat and LiveKit Agents both solve the voice runtime (audio in, VAD, turn detection, streaming STT, LLM, TTS, interruption) so that we do not hand-build it, and both can load MCP tools in about one line according to a third-party comparison. The deeper question for ME3 is whether the voice runtime should be a framework or our own thin loop, since the spec asks us to build the ASR and TTS MCP servers ourselves.

|  | Pipecat | LiveKit Agents |
| --- | --- | --- |
| Architecture | Frame-processor pipeline (STT, LLM, TTS), transport-agnostic | Agent joins a WebRTC room on a LiveKit media server |
| License | BSD-2-Clause | Apache-2.0 |
| Transports | Daily, LiveKit, WebSocket, SmallWebRTC, and a "Local" audio transport | LiveKit's own WebRTC layer; LiveKit Cloud or self-hosted server |
| Turn detection | Silero VAD plus open-source Smart Turn v3.x (ONNX, CPU) | Transformer turn detector (the outside course slides' "semantic turn detection") |
| Interruptions | Any user speech interrupts the bot by default. In 1.x, `user_turn_strategies` (for example `MinWordsUserTurnStartStrategy`) can require N words; the older `MinWordsInterruptionStrategy` was removed in 1.0 | Handled by the framework, per LiveKit's own comparison |
| MCP | `MCPClient` in `pipecat.services.mcp_service` with `register_tools(llm)`; stdio, SSE, and streamable HTTP (only streamable HTTP is integration-tested). No documented notification hook | One line as well, per the same comparison |
| Python | 3.11 minimum, 3.12 recommended on the Mac; a third-party Pipecat 1.3 build ran on Pi OS Debian 13 with Python 3.13 | Python agents SDK |
| Hosting cost | Framework free; managed hosting about $0.01 per minute; self-host VM about $50 to $80 per month | Framework free; LiveKit Cloud $0.01 per agent-session minute after plan allotments |

Sources: [Pipecat repo](https://github.com/pipecat-ai/pipecat), [Pipecat interruption strategies](https://docs.pipecat.ai/server/utilities/interruption-strategies), [Pipecat Smart Turn](https://docs.pipecat.ai/server/utilities/turn-detection/smart-turn-overview), [LiveKit's own comparison](https://livekit.io/field-guides/guide/livekit-vs-pipecat) (it favors LiveKit), and the [Fora Soft comparison](https://www.forasoft.com/blog/article/pipecat-vs-livekit-agents) (dated October 2026).

### Reading the evidence

- The outside course slides use LiveKit for their examples (`AgentSession(vad=silero.VAD.load(), stt=..., llm=..., tts=...)`), so LiveKit matches that course's vocabulary, including the stateful client-server design and the WebRTC transport. That course is not part of Prof's lectures, so this is a point of familiarity, not a requirement.
- LiveKit's room model needs a media server. That is useful for multi-party calls and phones, and extra operations work for a single Pi talking to one cloud backend. Pipecat's transport-agnostic pipeline is lighter for a single-user device, per the Fora Soft comparison.
- Neither framework's documentation I read mentions Raspberry Pi. Pipecat does list a "Local" audio transport and an ESP32 client SDK, but whether the full pipeline runs comfortably on a Pi 5 is something we must test.
- Both frameworks assume the ASR and TTS are service plugins. ME3 wants them as MCP servers that we build. The architecture review resolves this: MCP carries control (start, stop, duck, events) and audio stays on a local side channel. The extra local hop must be timed in the first spike, with a fallback of thin Pipecat services calling the providers directly.
- Pipecat's `MCPClient` does not surface server notifications, so a small `VoiceMCPBridge` using the raw MCP SDK client is needed. A pull request (5624) widens support to MCP SDK 2.x; its merge status is unverified, so pin Pipecat and the SDK together or use `mcp<2`.

## Raspberry Pi 5 baseline

The VCM exercise already settled most of the hardware choices, so ME3 should reuse them and add only what a streaming, network-bound agent needs: a reliable link, a clean audio path, and a way to keep latency measurable on the device itself.

### What carries over from the VCM exercise

| Item | VCM decision | Why it matters for ME3 |
| --- | --- | --- |
| Board | Tier 2 plan uses a Raspberry Pi 5 (Cytron Beginner Kit V2); Tier 1 prototyped on a borrowed Pi 4 with 4 GB | ME3 names the Pi 5; the Pi 4 is a fallback for early tests only |
| Audio | USB conferencing speakerphone (Anker PowerConf S3 class) replacing a separate mic and speaker | One device gives mic, speaker, and usually hardware echo cancellation, which barge-in depends on |
| Output | Audio only, no display | Matches a hands-free agent; no screen to update |
| Power | Portable USB-C PD power bank (a UPS HAT was deferred) | Remote operation needs stable power; brownouts show up as dropped audio |
| Actions | Spotify Web API (Premium account), Xiaomi bulb and plug at home, Bluetooth calling to an iPhone 15 as a stretch | These map directly to MCP servers for the ME2 task set |
| Dev and training | Mac M2 laptop for development, UP's DGX cluster for heavy work | Same split as ME3: develop locally, offload models |

### What changes in ME3

- **The Pi runs a client, not a model.** VAD is the only neural network on the device, so the Pi's job is capture, VAD, playback, and a persistent streaming connection. In the VCM exercise the model itself ran on the Pi; here the network is the dependency.
- **A new failure mode: the network.** The spec says the agent must run reliably in a remote location (class note 8). A dropped Wi-Fi or cellular link must be detected, announced to the user with a locally cached phrase, and recovered without losing the session.
- **Price of the board.** The Pi 5 lists at $110 (4 GB) or $175 (8 GB) after the 2026 increases.
- **Echo is now a first-class problem.** The VCM was push-to-talk. ME3 is open-mic while the agent speaks, so the speakerphone's echo cancellation (or a software canceller) decides whether barge-in works at all.

### Measure-first checklist on the Pi 5

None of these have published numbers for our setup. The Pi 5 has four CPU cores, and the VAD, turn detector, audio I/O, and networking all share them, so each item below is a measurement we should take before locking the design.

- [ ] Silero VAD real-time factor and CPU use on the Pi 5, per 32 ms audio frame
- [ ] Smart Turn v3 ONNX inference time on one Pi 5 core (cloud figures are 12.6 to 94.8 ms; my Pi estimate is 60 to 120 ms)
- [ ] Python and Pipecat install on Pi OS Trixie (Python 3.13), including PyAudio and portaudio
- [ ] Cost of the extra local hop through our MCP voice servers, on loopback
- [ ] PipeWire echo-cancel (WebRTC backend) CPU cost
- [ ] Round-trip time from the Pi to each candidate ASR, TTS, and LLM endpoint, from the actual venue network
- [ ] Echo cancellation: does the speakerphone alone prevent self-interruption at normal volume
- [ ] Playback buffer size and the time from "stop" command to silence at the speaker
- [ ] Idle and active CPU temperature and throttling during a 30-minute session
- [ ] Reconnect time after a deliberate 10-second network drop

## Benchmarks and latency method

The class agreed to start from Full-Duplex-Bench because it already measures the interaction qualities ME3 cares about, and it streams pre-recorded audio into the system under test, which works for a cascaded agent too. It does not measure tool use or task success, so ME3 needs a small benchmark of its own on top.

### What the existing benchmarks cover

| Benchmark | Scenarios | Key metrics | Gaps for ME3 |
| --- | --- | --- | --- |
| Full-Duplex-Bench v1 (Mar 2025) | Pause handling, backchannel, smooth turn-taking, user interruption | Takeover rate (TOR); backchannel frequency and JSD; response latency; GPT-4o score and latency after interruption | Counts the AI's backchannels, not the human's; no false-interruption rate; no tool use |
| Full-Duplex-Bench v1.5 (2026, same repository) | User interruption, user backchannel, talking to others, background speech | Respond, Resume, Uncertain, Unknown rates per scenario; stop latency; response latency; prosody change | Fixes the human-backchannel and addressee gaps raised in the class chat; still no tool use |
| [Full-Duplex-Bench v2](https://arxiv.org/abs/2510.07838) | Multi-turn: daily conversation, corrections, entity tracking, safety | Turn-taking fluency, multi-turn instruction following, task competence, via an automated examiner with Fast and Slow pacing | Closest to task evaluation among the first three. It needs a WebRTC peer, which Pipecat's SmallWebRTC transport can likely provide (unverified) |
| Full-Duplex-Bench v3 (2026) | Tool use with real-world disfluency: self-corrections, multi-step chains | Pass@1 and argument accuracy. GPT-Realtime scores 0.600 pass@1; a cascaded baseline had the highest latency at 10.12 s | Closest to our ten tasks; disfluency is the main failure mode, so we add self-correction clips |
| Service Duplex Bench | Customer-service scenarios | Used by NVIDIA for PersonaPlex | Not yet released, per the PersonaPlex page |

The v1.5 paper sets the response-gap bar for human-like repair at 1.5 seconds or less, and its expected-behavior table says the agent should Respond to an interruption but Resume after a backchannel, a side conversation, or background speech. The Resume cases are exactly the false-interruption and "should I be answering this" tests discussed in the class chat, so v1.5 covers the no-wake-word worry directly.

### Licence, repository, and adapter

Full-Duplex-Bench is one repository (`DanielLin94144/Full-Duplex-Bench`) with `v1_v1.5`, `v2`, and `v3` folders, under CC BY-NC 4.0: fine for coursework, not for commercial benchmarking claims without permission. DuplexCascade, a VAD-free cascaded system that is competitive on turn-taking benchmarks, is MIT.

Versions 1 and 1.5 are offline: they supply a user-channel wav and expect a time-aligned agent-channel wav. An adapter streams the wav into the pipeline through a file-paced input transport and records every output audio frame, with its wall-clock offset, into a same-length wav, then runs the evaluation scripts.

### Reference numbers from v1.5 (user interruption scenario)

OpenAI's GPT-4o Realtime API (a speech-to-speech service) stops in 0.23 s but takes 1.50 s to answer; Freeze-Omni, the open cascaded system, stops in 1.42 s. These are the numbers a Pi agent has to compete with.

| System | Respond rate (higher is better) | Stop latency (s) | Response latency (s) |
| --- | --- | --- | --- |
| GPT-4o Realtime | 0.78 | 0.23 | 1.50 |
| Freeze-Omni (cascaded, open) | 0.72 | 1.42 | 1.35 |
| Moshi | 0.50 | 1.16 | 1.47 |
| Gemini Live | 0.33 | 2.20 | 2.62 |
| Amazon Nova Sonic | 0.24 | 2.25 | 2.75 |

For the backchannel scenario in the same paper, the Resume rate (ignoring the "mm-hmm" and carrying on) was 0.98 for Sonic, 0.93 for Gemini, 0.80 for Freeze-Omni, 0.70 for GPT-4o, and 0.06 for Moshi.

### Candidate latency method for ME3

The outside course guide (Anthony's AI-generated notes on the DeepLearning.AI course) and its slides give a shared vocabulary; Prof's Efficiency deck gives the measurement discipline (warm up, time in a loop, report percentiles, measure end to end because proxies mislead).

| Metric | Definition | Where it is stamped |
| --- | --- | --- |
| End-to-end response latency | End of user speech to first audible agent audio | Injected test audio end time, to the speaker output capture |
| Stop latency | Onset of user speech during agent audio to agent audio going silent | Same, plus the Pi's playback-stop event |
| Flush time | Interruption detected to LLM stream, TTS stream, and playback queue all cancelled (guide target: under 50 ms) | Pi and servers |
| EOU latency | User stops speaking to the end-of-turn event firing | Pi |
| ASR finalization, LLM TTFT, TTS TTFB | Per-stage times, the LiveKit metric names | Servers, with the Pi's clock for the network legs |
| Takeover rate on pauses, Resume rate on backchannel, side talk, and background speech | v1 and v1.5 definitions | Scripted test audio |
| Tool-call success and task completion | Our own: correct tool, correct arguments, correct final state, on the 10 base tasks plus the complex task | Test harness |
| Cost per conversation minute | Provider bills divided by minutes of the session | Provider dashboards or token counters |
| Cost per successful task | Total session spend divided by successful tasks | Usage metadata. No standard voice benchmark reports this, so it is our own contribution |

Targets quoted in the sources disagree, so I would report distributions and let the table tell the story rather than pick one number. The outside course slides say humans expect a reply in about 236 ms on average and a streaming stack lands between about 540 ms and 1.6 s. The AI-generated guide says under 600 ms is exceptional and over 1,200 ms is sluggish; check that against the course before citing it. The v1.5 paper treats 1.5 s or less as human-like repair.

One practical point from the class chat that I would keep: record our own scripted audio (commands with pauses, backchannels, side talk, and interruptions at known timestamps) and replay it into the Pi's input. Known timestamps make every latency number reproducible instead of depending on a person speaking differently each run.

### Cartesia over-commit on interruption was a bench estimate (Nov 4, 2026)

On Nov 3, `bench.interruption_test --tts cartesia` reported 4 words committed past what was heard in immediate mode and 5 in duck mode, against 1 on `fake_words`. The cause is the bench, not Cartesia or Pipecat. Analysis from the two event logs, no new paid calls.

| Run | Mode | First loud sample to last loud sample | Stop latency | Committed (reported) | Heard at 850 chars/min |
| --- | --- | --- | --- | --- | --- |
| `20261103-121404-05fcde` | immediate | 2.72 s | 279 ms | 10 words, up to "lived" | 6 |
| `20261103-121420-e5b110` | duck | 3.76 s | 1,298 ms | about 13 | 8 to 9 |

1. **The heard estimate was wrong for Cartesia (the cause).** For any TTS other than the fakes, the bench turned played seconds into characters at a fixed 850 characters per minute (14.2 a second), and never used the word timestamps. Cartesia Sonic speaks faster. For the context to hold "...named Ana who lived" after 2.72 s, the voice had reached character 49 to 54, which is 18.0 to 19.9 characters a second. The duck run gives 17.0 to 18.9. A separate bound comes from the `audio_hop` events of the duck run: the 470-character story arrived at about 8 s of audio per second, then the arrival rate fell in the window before the cancel, so the whole story was 24.4 to 28.6 s of audio, 16.4 to 19.3 characters a second including the pauses between sentences. At 850 a minute it would be 33.2 s. At 16.4 to 19.3 characters a second, the immediate run heard 8 or 9 words, so it committed 1 or 2 extra, the same as `fake_words`. Two runs of different length give the same implied pace, which fits a rate error. A timestamp offset would add a fixed number of words instead.
2. **Played seconds also skipped silent chunks.** `played_s` sums only voiced output chunks, and Cartesia speech has quiet chunks between words and phrases that the constant fake tone never has. That shrinks "heard" further and likely explains why the duck run's figure was 5 rather than 4. The fix measures wall time from the first loud sample instead.
3. **Cartesia's timestamps running ahead: not supported.** Pipecat 1.13's `CartesiaTTSService` passes each word's `start` through `_normalize_word_timestamps` unchanged (it only strips SSML tags), and `TTSService` sets the timestamp baseline on the first audio chunk, the same path as `fake_words`. The logs carry no per-word events, so this is a reading of the code, not a measurement.
4. **Output buffering: not in these runs.** The bench plays into `PacedNullOutputTransport`, which blocks each write for the chunk's length with a 20 ms lead, so playback starts on the first chunk and never underruns while Cartesia sends 8 s of audio a second. `audio_hops` records no writes on this transport (`writes` is 0), so it gives arrival only. On a real device the device buffer does add latency, which is not measured here.
5. **Pipecat's truncation: works.** Words are released at their start time, so the word in progress is committed. That is the 1 extra word on both TTS.

Fix: `PacedNullOutputTransport` keeps each timed `TTSTextFrame`, and the bench counts a word as heard once the next word has started, measured from the first loud sample to the last loud sample before the cut (`heard_source: word_timestamps`). The pace estimate stays as the fallback for TTS without timestamps and is reported as `heard_words_pace`. A new `fake_words_fast` stage (45 ms a character, 1,333 a minute) reproduces the old error on fakes: the pace estimate says 6 heard words where the timestamps say 11, and the real over-commit is 1 (`tests/test_interruption.py`).

Confirmed live, Nov 4 (`20261104-201942-2a1054`, `--tts cartesia`, immediate, $0.0246): 2.68 s played, 9 words heard from the timestamps where the pace estimate says 6, 10 committed, so the over-commit is 1 word. Stop took 245 ms. The figure still trusts Cartesia's own timestamps for "heard". Checking those against the audio needs a run that lets the story play to the end and compares the last word's start with the audio length.

## Cost model

A cascaded stack of streaming ASR, a small LLM, and streaming TTS costs about $1.10 to $1.61 per hour of conversation at list prices, roughly a quarter to a half of the bundled voice-agent APIs. ASR and TTS carry almost all of it: in the cheapest stack, TTS is about half, ASR about 40%, and the LLM about 8%.

| Stack | ASR | TTS | LLM | Total per hour |
| --- | --- | --- | --- | --- |
| Cheapest cascaded (Deepgram Nova-3 and Aura-2, Groq gpt-oss-20b), 40% speaking | about $0.46 | about $0.55 | about $0.09 | about $1.10 |
| Higher-end cascaded (Cartesia, Haiku 5.5), 40% speaking | about $0.46 | about $0.9 to $1.0 | about $0.09 | about $1.61 |
| Same stack, agent speaking 25% of the hour | about $0.39 | about $0.41 to $0.88 | under $0.05 | about $0.8 to $1.3 |
| Speech-to-speech (Gemini Live) | per-minute bundle | per-minute bundle | bundled | about $1.38 |

The rows are computed from list prices and the stated assumptions, not measured. Times are for an active hour, not a day of idling.

The speech-to-speech bar is not a like-for-like comparison. It is derived from Google's stated Live API audio rates (about $0.005 per minute in and $0.018 per minute out), and I have not checked whether it supports our MCP tool path. OpenAI's realtime models are priced per audio token ($32 input and $64 output per 1M tokens for gpt-realtime-2.1), but the page gives no tokens-per-minute figure, so they are left out.

### Assumptions behind the bars

- Streaming ASR bills for every minute of audio sent, with no VAD gating. Left ungated for 24 hours, Flux would cost about $9.36 per device per day.
- The agent speaks 40% of the time at 775 characters per audio minute (Cartesia's 750 to 800), about 310 characters per conversation minute.
- Four turns per minute, each sending 3,000 input tokens and returning 100 output tokens, with no prompt caching.
- Prices: Deepgram Nova-3 at the regular $0.0077 per minute, Aura-2 at $0.030 per 1,000 characters, Cartesia Sonic at the $50 per 1M characters Pro rate, ElevenLabs Flash at $0.04 per 1,000 characters, and the LLM prices from the LLM section.
- Not included: network or hosting for the Pi, a LiveKit or Pipecat hosting fee ($0.01 per minute if managed), and the one complex task, whose token use depends on the task.

### Margin and hardware

A Pi 5 (4 GB $110, 8 GB $175), a speakerphone (about $165), and about $30 of extras come to roughly $300 per unit. If an active hour costs about $1.10 to $1.61, a subscription charging $3, $5, or $8 per active hour would leave gross margins of about 63/46, 78/68, and 86/80 percent (low-cost and high-end stack). These are illustrative and exclude support, payment fees, and idle time.

### Levers, largest first

| Lever | Effect | Basis |
| --- | --- | --- |
| Keep the harness prompt lean | A harness that sends 20,000 input tokens per turn would cost about $0.008 per minute on Haiku 5.5 at four turns a minute, nearly six times the $0.0014 assumed above | Fast.io review of Hermes Agent overhead; our prompt size is unmeasured |
| Say less | Every 100 spoken characters cost about $0.003 on Aura-2 and $0.004 on ElevenLabs Flash | Provider price pages |
| Send ASR only the speech | If speech is about half the audio, ASR spend roughly halves. With VAD gating and a 300 to 500 ms pre-roll ring buffer, ASR is billed only on speech; this is the key lever for an always-on device | Outside course guide; confirm each provider bills by audio sent |
| Prompt caching | Haiku 5.5 cache reads cost $0.01 per 1M tokens versus $0.10 for fresh input | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Use free credits for development | Deepgram's $200 credit covers roughly 190 hours of Nova-3 plus Aura-2 at the assumed mix (sources conflict on whether it expires); Cartesia's free tier is for personal use only; Google's Flash, Flash-Lite and Live API have a free tier whose content may be used to improve Google's products | Provider pages |
| Reserve the strong model for the complex task | Keep the conversation on a cheap model and switch up only for the evaluation task, with a token budget | Design choice |
| Local fast path | Closed-vocabulary commands (stop, pause, volume, lights, timers) handled on the device with no ASR or LLM | Design choice |
| Self-host on the DGX | No per-token bill, but queue delay and no control over time to first token | Class chat |

## Licensing and sellability

This is a checklist for a Philippine IP lawyer, not legal advice. Licences were read from the files or pages named unless marked.

| Component | Licence | Note |
| --- | --- | --- |
| Pipecat, Smart Turn v3 | BSD-2-Clause | Low risk |
| Silero VAD | MIT | Low risk |
| TEN VAD | Apache-2.0 plus a clause against competing with Agora and limiting use to your own application | Do not use |
| FastMCP (4.1.1) | Apache-2.0 | Low risk |
| MCP Python SDK (2.2.0) | MIT | Low risk |
| onit, baby-onit | Apache-2.0 | Low risk |
| Qwen3-TTS code | Apache-2.0 | Weights not verified |
| DuplexCascade | MIT | Low risk |
| Full-Duplex-Bench | CC BY-NC 4.0 | Coursework only without permission |
| NemotronLabs VoiceChat weights | Research-only (third-party) | Do not ship |
| LiveKit Agents, Moshi, PersonaPlex, Qwen weights | Not verified | Check before citing |

- **Provider terms.** Only two were verified: ElevenLabs' free tier is non-commercial and Cartesia's free tier is for personal use. Bring-your-own-keys is the safest way to sell until the rest are checked.
- **Spotify.** Terms grant private personal use and bar training machine learning on its content. Development mode allows 5 users and needs Premium. Extended access is for organizations with at least 250K monthly active users. So it is demo only; alternatives (a Subsonic-compatible server, internet radio, local files) were not researched in depth.
- **Privacy.** The measures built so far are listed in `docs/privacy.md`.

## Improvements from the literature

Each candidate has a metric; none is known to be novel.

1. **Barge-in position as feedback.** Log where users cut in, then shorten replies for users who interrupt often. Metric: spoken characters per turn with task success unchanged. Most promising; no direct match found.
2. **Cost-aware addressing cascade.** Tiered gates (VAD, tiny classifier, cheap ASR, LLM) with a spend budget and a pre-roll buffer. Metric: false triggers per hour and ASR dollars per hour.
3. **Undo window for irreversible tools.** Use MCP tool annotations to speculate only on safe tools and delay destructive ones about 5 s so a barge-in can cancel. Metric: wrong-action rate on "no wait, stop" clips.
4. **Resume policy after interruption.** Classify the interruption (backchannel, clarification, topic change, cancel) and resume, summarize, or drop. Metric: Respond and Resume rates in v1.5.
5. **Local fast path.** A small on-device matcher for closed-vocabulary commands. Metric: cost and latency for those commands.

Other findings: v3 shows disfluency is the main failure mode, DuplexCascade shows a cascade is competitive, and Smart Turn's 3.75% false "complete" rate needs a recovery phrase.

## Risks, open questions, and sources

### Risks

| Risk | Why it matters | Mitigation to evaluate |
| --- | --- | --- |
| Echo triggers self-interruption | Open-mic barge-in needs the agent not to hear itself | Speakerphone with hardware echo cancellation; measure before building more |
| MCP tools are request and response, barge-in is a stream | An ASR or TTS "tool" that blocks until done cannot be cancelled in tens of milliseconds | Let the MCP servers hold the stream and expose start, stop, and event tools, or keep streaming outside MCP and expose only control tools; decide in the architecture review |
| Slow shared DGX endpoint | The class reports onit is slow because of DGX load; time to first token is the biggest block in the budget | Commercial API for the conversation path, DGX for non-latency work; time each endpoint separately |
| Network drops at a remote site | The spec requires reliable remote operation | Cached local phrases for "connection lost", reconnect logic, and a deliberate-drop test |
| Cheap models fumble tool arguments | A voice agent that calls the wrong tool is worse than a slow one | Own tool-call success test on the 10 base tasks, per model |
| No wake word, so false triggers | Side talk and TV audio can trigger the agent | v1.5's talking-to-others and background-speech scenarios; Ailene's fallback of a wake word after idle |
| Pipecat has no notification hook for MCP | Server events need a bridge | `VoiceMCPBridge` on the raw MCP SDK; spike first |
| MCP SDK 2.x changes | Streamable HTTP can break without the right pins | Pin Pipecat and the SDK together |
| Pipecat API churn | Pre-1.0 examples use removed arguments that are silently ignored | Use only 1.x APIs |
| Smart Turn false "complete" | 3.75% for English cuts people off | Incomplete-turn fallback and a recovery phrase |
| Licence limits on a product | TEN VAD, Spotify, and a non-commercial benchmark | See the licensing section |
| Unverified vendor numbers | Latency and accuracy claims come from vendors and press | Reproduce from the Pi on the venue network before citing |
| Security of tools that act | Calls, purchases, and shell commands run from a voice | Least privilege, approval for high-stakes actions, and logging every tool call, per the Agents deck |

### Open questions

- What is onit's own overhead per request? It has no programmatic API, so we wrap it as a subprocess and measure.
- Is Smart Turn viable on the Pi 5, and what is Silero's real-time factor there?
- Can Pipecat's SmallWebRTC transport drive Full-Duplex-Bench v2's examiner? (v3 covers tool use.)
- Is the PR that widens Pipecat's MCP SDK support merged?
- Which provider terms (Deepgram, Groq, Anthropic, Google, OpenRouter, Ollama Cloud) allow resale and what do they say about audio retention?
- How is the no-wake-word requirement graded: by false-trigger rate, or by a wake word after idle being acceptable?
- Which 10 base commands and which complex task will the class settle on at Thursday's meeting?
- A music source that could back a product (see licensing).

### Sources

Prof. Atienza's Agents and Efficiency decks are the course material. Outside references, not part of Prof's lectures and used for ideas only: the LiveKit and DeepLearning.AI voice-agent slides and Anthony's AI-generated course guide. Also used: the class group chat (Oct 20 to 21, 2026) and the Full-Duplex-Bench v1 ([arXiv 2503.04721](https://arxiv.org/abs/2503.04721)) and v1.5 papers.

Added in this revision (verification pass): [Pipecat MCPClient reference](https://reference-server.pipecat.ai/en/latest/api/pipecat.services.mcp_service.html), [Pipecat user turn strategies](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies), [Smart Turn repository](https://github.com/pipecat-ai/smart-turn), [Pipecat PR 5624](https://github.com/pipecat-ai/pipecat/pull/5624), [Pipecat issue 5605](https://github.com/pipecat-ai/pipecat/issues/5605), [Pipecat issue 670](https://github.com/pipecat-ai/pipecat/issues/670), [Full-Duplex-Bench repository](https://github.com/DanielLin94144/Full-Duplex-Bench), [v3 paper](https://arxiv.org/pdf/2604.04847), [DuplexCascade](https://github.com/sbintuitions/DuplexCascade), [FastMCP changelog](https://gofastmcp.com/changelog), [Spotify terms](https://developer.spotify.com/terms), [Spotify quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes), [TEN VAD licence](https://github.com/TEN-framework/ten-vad/blob/main/LICENSE), [Raspberry Pi 5 product brief](https://pip-assets.raspberrypi.com/categories/892-raspberry-pi-5/documents/RP-008348-DS-6-raspberry-pi-5-product-brief.pdf).

Web pages opened for the first version of this document:

- [onit](https://github.com/sibyl-oracles/onit), [onit architecture](https://github.com/sibyl-oracles/onit/blob/main/docs/ARCHITECTURE.md), [baby-onit](https://github.com/sibyl-oracles/baby-onit), [roatienza/Deep-Learning-Experiments](https://github.com/roatienza/Deep-Learning-Experiments), [roatienza/benchmark](https://github.com/roatienza/benchmark)
- [PersonaPlex](https://research.nvidia.com/labs/adlr/personaplex/), [NemotronLabs VoiceChat coverage (AI Weekly)](https://aiweekly.co/alerts/nvidia-opens-nemotronlabs-voicechat-11b-with-tool-calling), [Nemotron VoiceChat explainer (OrcaRouter)](https://www.orcarouter.ai/blog/nvidia-nemotron-voicechat-11b-explained); the NVIDIA repository page was blocked to my fetch tool, and the Kyutai MoshiRAG post returned only its title
- [Pipecat](https://github.com/pipecat-ai/pipecat), [Pipecat supported services](https://docs.pipecat.ai/server/services/supported-services), [Smart Turn](https://docs.pipecat.ai/server/utilities/turn-detection/smart-turn-overview), [interruption strategies](https://docs.pipecat.ai/server/utilities/interruption-strategies), [LiveKit vs Pipecat (LiveKit)](https://livekit.io/field-guides/guide/livekit-vs-pipecat), [Pipecat vs LiveKit (Fora Soft)](https://www.forasoft.com/blog/article/pipecat-vs-livekit-agents), [LiveKit STT docs](https://docs.livekit.io/agents/models/stt/)
- [TEN VAD](https://huggingface.co/TEN-framework/ten-vad), [Full-Duplex-Bench v2](https://arxiv.org/abs/2510.07838), [Full-Duplex-Bench code](https://github.com/DanielLin94144/Full-Duplex-Bench) (repository now opened)
- [Qwen3-TTS paper](https://arxiv.org/html/2601.15621), [Qwen3-ASR on Baseten](https://www.baseten.co/library/qwen3-asr-1-7b/), [Hermes Agent review](https://www.fast.io/resources/hermes-agent-review-2026.md), [agent harness comparison (Eden AI)](https://www.edenai.co/post/best-ai-agent-harnesses-comparison-guide)
- Pricing, fetched Oct 20 to 21, 2026: [Deepgram](https://deepgram.com/pricing), [Cartesia](https://cartesia.ai/pricing), [ElevenLabs](https://elevenlabs.io/pricing/api), [Groq](https://console.groq.com/docs/models), [Google Gemini](https://ai.google.dev/gemini-api/docs/pricing), [Anthropic](https://platform.claude.com/docs/en/about-claude/pricing), [OpenAI](https://developers.openai.com/api/docs/pricing), [BenchLM](https://benchlm.ai/llm-pricing)
