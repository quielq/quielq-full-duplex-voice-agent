// The Spotify player inside the face (ADR-031). Served only when ui.spotify_player is on.
// It only plays: choosing and pausing stay on the music tools, behind the tool policy.
(() => {
  const report = (state, detail = "") =>
    fetch("/api/spotify-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state, detail }),
    }).catch(() => {});

  window.onSpotifyWebPlaybackSDKReady = () => {
    const player = new Spotify.Player({
      name: "ME3",
      volume: 1,
      getOAuthToken: (give) =>
        fetch("/api/spotify-token")
          .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
          .then((body) => give(body.token))
          .catch((e) => report("token_error", e.message)),
    });
    player.addListener("ready", () => report("ready"));
    player.addListener("not_ready", () => report("not_ready"));
    for (const name of ["initialization_error", "authentication_error", "account_error", "playback_error"]) {
      player.addListener(name, ({ message }) => report(name, message));
    }
    player.addListener("autoplay_failed", () => report("autoplay_failed"));
    player.connect();
    document.addEventListener("pointerdown", () => player.activateElement(), { once: true });
  };

  const sdk = document.createElement("script");
  sdk.src = "https://sdk.scdn.co/spotify-player.js";
  sdk.onerror = () => report("sdk_load_failed");
  document.head.append(sdk);
})();
