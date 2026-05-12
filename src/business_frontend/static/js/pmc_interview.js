/* PMC voice interview — browser client.
 *
 * Joins a LiveKit room with a server-issued participant token, publishes
 * the owner's mic, subscribes to the agent's TTS audio, renders the live
 * transcript and coverage progress, and listens for a redirect data
 * message from the agent on call-end.
 *
 * Defense in depth:
 *  - Watchdog poll every 5s in case the data-message redirect is lost.
 *  - Mic permission errors surface to the on-screen banner, not a silent fail.
 *  - "End interview" sends a final data message to the agent so the agent
 *    can flush its transcript before disconnecting.
 *
 * Disabled-visible UX: the start button stays disabled until both
 * disclosure ack AND livekit-client SDK load have happened. Failures in
 * either path keep the button disabled with a visible reason.
 */

(function () {
  "use strict";

  const cfg = window.PMC_INTERVIEW;
  if (!cfg) {
    console.error("[pmc_interview] window.PMC_INTERVIEW missing — template error");
    return;
  }

  // ── DOM handles ────────────────────────────────────────────────────
  const el = {
    disclosureAck: document.getElementById("disclosure-ack"),
    preCall: document.getElementById("pre-call"),
    preCallError: document.getElementById("pre-call-error"),
    inCall: document.getElementById("in-call"),
    postCall: document.getElementById("post-call"),
    btnStart: document.getElementById("btn-start"),
    btnEnd: document.getElementById("btn-end"),
    micState: document.getElementById("mic-state"),
    micLevel: document.getElementById("mic-level"),
    callState: document.getElementById("call-state"),
    callTimer: document.getElementById("call-timer"),
    transcript: document.getElementById("transcript"),
    progressDots: document.getElementById("progress-dots"),
    weight3Remaining: document.getElementById("weight3-remaining"),
  };

  // ── State ──────────────────────────────────────────────────────────
  const state = {
    room: null,
    micTrack: null,
    micAnalyser: null,
    callStartMs: null,
    timerHandle: null,
    statusPollHandle: null,
    audioContext: null,
    redirectFired: false,
  };

  // ── Helpers ────────────────────────────────────────────────────────
  function setMicPill(text, cls) {
    el.micState.className = "state-pill" + (cls ? " " + cls : "");
    el.micState.innerHTML = '<span class="dot"></span> ' + text;
  }

  function setCallPill(text, cls) {
    el.callState.className = "state-pill" + (cls ? " " + cls : "");
    el.callState.innerHTML = '<span class="dot"></span> ' + text;
  }

  function showPreCallError(msg) {
    el.preCallError.style.display = "";
    el.preCallError.textContent = msg;
  }

  function appendTurn(speaker, text) {
    // Speaker is "you" or "agent". `text` is plain string.
    const turn = document.createElement("div");
    turn.className = "transcript-turn " + speaker;
    const label = document.createElement("div");
    label.className = "speaker";
    label.textContent = speaker === "agent" ? "Interviewer" : "You";
    const body = document.createElement("div");
    body.textContent = text;
    turn.appendChild(label);
    turn.appendChild(body);
    // Drop the placeholder paragraph on first real turn.
    const placeholder = el.transcript.querySelector("p.text-muted");
    if (placeholder) placeholder.remove();
    el.transcript.appendChild(turn);
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  function renderProgressDots(total, coveredKeys, currentKey, weight3RemainingKeys) {
    // total is # of qualitative questions reported by the agent
    el.progressDots.innerHTML = "";
    for (let i = 0; i < total; i++) {
      const d = document.createElement("span");
      d.className = "dot";
      // We don't have per-key info on the browser side; the agent sends
      // a coveredCount + currentIndex + weight3Remaining count instead.
      el.progressDots.appendChild(d);
    }
    if (Array.isArray(coveredKeys)) {
      for (let i = 0; i < coveredKeys; i++) {
        if (el.progressDots.children[i]) el.progressDots.children[i].classList.add("covered");
      }
    }
    if (typeof currentKey === "number" && el.progressDots.children[currentKey]) {
      el.progressDots.children[currentKey].classList.add("current");
    }
    if (typeof weight3RemainingKeys === "number") {
      el.weight3Remaining.textContent =
        weight3RemainingKeys > 0
          ? weight3RemainingKeys + " important topic" + (weight3RemainingKeys === 1 ? "" : "s") + " to go"
          : "All important topics covered — we're wrapping up.";
    }
  }

  function formatElapsed(ms) {
    const total = Math.floor(ms / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function startTimer() {
    state.callStartMs = Date.now();
    state.timerHandle = setInterval(function () {
      el.callTimer.textContent = formatElapsed(Date.now() - state.callStartMs);
    }, 1000);
  }

  function stopTimer() {
    if (state.timerHandle) clearInterval(state.timerHandle);
    state.timerHandle = null;
  }

  // ── Mic level meter (visual feedback that mic is actually working) ─
  function attachMicMeter(track) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      state.audioContext = ctx;
      const source = ctx.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      state.micAnalyser = analyser;
      const data = new Uint8Array(analyser.frequencyBinCount);
      function tick() {
        if (!state.micAnalyser) return;
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        const pct = Math.min(100, rms * 400);
        el.micLevel.style.width = pct + "%";
        requestAnimationFrame(tick);
      }
      tick();
    } catch (e) {
      console.warn("[pmc_interview] mic meter failed:", e);
    }
  }

  // ── Watchdog: poll /voice/status in case the data-message redirect is lost
  function startStatusWatchdog() {
    state.statusPollHandle = setInterval(async function () {
      if (state.redirectFired) return;
      try {
        const resp = await fetch(cfg.statusUrl, { credentials: "same-origin" });
        if (!resp.ok) return;
        const body = await resp.json();
        if (body.status === "voice_completed" || body.status === "voice_partial") {
          // Agent has finalized; navigate.
          state.redirectFired = true;
          stopStatusWatchdog();
          navigateToReview();
        }
      } catch (_) {
        // Network blip — try again next tick.
      }
    }, 5000);
  }

  function stopStatusWatchdog() {
    if (state.statusPollHandle) clearInterval(state.statusPollHandle);
    state.statusPollHandle = null;
  }

  function navigateToReview() {
    showPostCall();
    window.setTimeout(function () {
      window.location.href = cfg.redirectTo;
    }, 400);
  }

  function showInCall() {
    el.preCall.style.display = "none";
    el.inCall.style.display = "";
    el.postCall.style.display = "none";
  }

  function showPostCall() {
    el.preCall.style.display = "none";
    el.inCall.style.display = "none";
    el.postCall.style.display = "";
  }

  // ── Disclosure + start button gating ───────────────────────────────
  function updateStartGating() {
    const acked = el.disclosureAck.checked;
    const sdkReady = typeof window.LivekitClient !== "undefined" || typeof window.LiveKit !== "undefined";
    el.btnStart.disabled = !(acked && sdkReady);
    if (!sdkReady) {
      showPreCallError(
        "LiveKit JS SDK didn't load. Refresh the page; if it still fails, contact support."
      );
    } else {
      el.preCallError.style.display = "none";
    }
  }
  el.disclosureAck.addEventListener("change", updateStartGating);
  // Poll for SDK readiness for ~5s before giving up.
  let sdkPollTicks = 0;
  const sdkPoll = setInterval(function () {
    sdkPollTicks++;
    if (typeof window.LivekitClient !== "undefined" || typeof window.LiveKit !== "undefined") {
      clearInterval(sdkPoll);
      updateStartGating();
    } else if (sdkPollTicks > 25) {
      clearInterval(sdkPoll);
      updateStartGating();
    }
  }, 200);

  // ── Connect & call lifecycle ───────────────────────────────────────
  async function startCall() {
    el.btnStart.disabled = true;
    setMicPill("Requesting mic permission…");

    const LK = window.LivekitClient || window.LiveKit;
    if (!LK) {
      showPreCallError("LiveKit SDK not available. Refresh the page.");
      el.btnStart.disabled = false;
      return;
    }

    const room = new LK.Room({
      adaptiveStream: true,
      dynacast: true,
    });
    state.room = room;

    // Wire room events BEFORE connect so we never miss them.
    room.on(LK.RoomEvent.ParticipantConnected, function (p) {
      if (p.identity && p.identity.indexOf("agent") === 0) {
        setCallPill("Listening", "live");
      }
    });
    room.on(LK.RoomEvent.TrackSubscribed, function (track, _pub, participant) {
      if (track.kind === "audio" && participant.identity.indexOf("agent") === 0) {
        // Attach the agent's audio so we can hear them.
        const audioEl = track.attach();
        audioEl.autoplay = true;
        document.body.appendChild(audioEl);
      }
    });
    room.on(LK.RoomEvent.DataReceived, function (payload, _participant, _kind, _topic) {
      try {
        const text = new TextDecoder().decode(payload);
        const msg = JSON.parse(text);
        handleAgentMessage(msg);
      } catch (e) {
        console.warn("[pmc_interview] bad data message:", e);
      }
    });
    room.on(LK.RoomEvent.Disconnected, function (reason) {
      console.info("[pmc_interview] room disconnected:", reason);
      // If the agent disconnected us with a graceful reason after sending
      // a redirect, navigate. Otherwise leave the watchdog to handle it.
      if (state.redirectFired) navigateToReview();
    });

    try {
      await room.connect(cfg.livekitUrl, cfg.token);
    } catch (e) {
      console.error("[pmc_interview] connect failed:", e);
      showPreCallError("Couldn't connect to the interview room: " + (e.message || e));
      setMicPill("Mic not yet active");
      el.btnStart.disabled = false;
      return;
    }

    // Publish mic.
    let micTrack;
    try {
      micTrack = await LK.createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
      });
      await room.localParticipant.publishTrack(micTrack);
    } catch (e) {
      console.error("[pmc_interview] mic publish failed:", e);
      const denied = e && (e.name === "NotAllowedError" || e.name === "PermissionDeniedError");
      showPreCallError(
        denied
          ? "Microphone permission was denied. Phone interview support is coming soon — for now, please allow mic access and refresh."
          : "Couldn't access your microphone: " + (e.message || e)
      );
      try { await room.disconnect(); } catch (_) {}
      setMicPill("Mic blocked", "error");
      el.btnStart.disabled = false;
      return;
    }

    state.micTrack = micTrack;
    attachMicMeter(micTrack);
    setMicPill("Mic active", "live");
    showInCall();
    setCallPill("Connecting", null);
    startTimer();
    startStatusWatchdog();
  }

  async function endCall(reason) {
    if (!state.room) return;
    el.btnEnd.disabled = true;
    setCallPill("Wrapping up", null);
    // Notify agent so it can flush the transcript before we disconnect.
    try {
      const payload = new TextEncoder().encode(
        JSON.stringify({ type: "end_requested", reason: reason || "user_clicked" })
      );
      await state.room.localParticipant.publishData(payload, { reliable: true });
    } catch (e) {
      console.warn("[pmc_interview] end signal publish failed:", e);
    }
    // Don't disconnect immediately — wait briefly for the agent to acknowledge.
    setTimeout(async function () {
      try { await state.room.disconnect(); } catch (_) {}
      stopTimer();
      // Watchdog will catch the final state and navigate.
    }, 1500);
  }

  function handleAgentMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    switch (msg.type) {
      case "agent_turn":
        appendTurn("agent", msg.text || "");
        setCallPill("Listening", "live");
        break;
      case "user_turn":
        appendTurn("you", msg.text || "");
        setCallPill("Thinking", null);
        break;
      case "coverage":
        // {type:"coverage", total: N, covered: M, current: K, weight3_remaining: J}
        renderProgressDots(msg.total, msg.covered, msg.current, msg.weight3_remaining);
        break;
      case "state":
        // {type:"state", pill: "Wrapping up"}
        if (msg.pill) setCallPill(msg.pill, msg.live ? "live" : null);
        break;
      case "redirect":
        // Agent has POSTed transcript back successfully; navigate.
        state.redirectFired = true;
        stopStatusWatchdog();
        navigateToReview();
        break;
      case "error":
        // Soft errors from the agent — e.g. TTS provider hiccup.
        console.warn("[pmc_interview] agent error:", msg.detail);
        break;
      default:
        // Forward-compat — ignore unknown messages.
        break;
    }
  }

  el.btnStart.addEventListener("click", startCall);
  el.btnEnd.addEventListener("click", function () { endCall("user_clicked"); });

  // Defensive: if the page is closed mid-call, try to notify the agent.
  window.addEventListener("beforeunload", function () {
    if (state.room && state.room.state === "connected") {
      try {
        const payload = new TextEncoder().encode(JSON.stringify({ type: "user_disconnected" }));
        state.room.localParticipant.publishData(payload, { reliable: true });
      } catch (_) {}
    }
  });
})();
