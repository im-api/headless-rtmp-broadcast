const { createApp, computed } = Vue;

    createApp({
      data() {
        return {
          
          encoderSettings: {
            audio_bitrate: "320k",
            video_bitrate: "800k",
            maxrate: "800k",
            bufsize: "1600k",
            video_fps: 24,
          },
token: localStorage.getItem("musicbot_session_token") || "",
          loginForm: {
            username: "",
            password: "",
          },
          loginStatus: "",
          loginStatusError: false,

          // Editing flags to avoid overwriting fields while user types
          editing: {
            rtmp: false,
            ffmpeg: false,
            overlay: false,
            playlist: false,
          },

          // Config fields
          rtmp: "",
          ffmpegPath: "",
          video: "",
          overlay: "",

          // Playlist visual order & durations
          playlistOrder: [],
          trackDurations: [],

          // Fetched player state
          state: {},

          // Logs
          logs: [],

          // File manager lists
          audioFilesList: [],
          videoFilesList: [],

          // Time bar / seek
          seekSlider: 0,
          timeBarMax: 0,
          seeking: false,

          // DnD
          dragIndex: null,

          _pollTimer: null,
        };
      },
      computed: {
        isLoggedIn() {
          return !!this.token;
        },
        prettyState() {
          return Object.keys(this.state).length
            ? JSON.stringify(this.state, null, 2)
            : "No state yet.";
        },
        logsText() {
          return this.logs.length ? this.logs.join("\n") : "No logs yet.";
        },
        statusPillClass() {
          const s = (this.state.status || "").toLowerCase();
          if (s === "playing") {
            return "border-emerald-500/60 bg-emerald-500/10 text-emerald-200";
          }
          if (s === "paused") {
            return "border-amber-500/60 bg-amber-500/10 text-amber-200";
          }
          if (s === "stopped") {
            return "border-slate-500/60 bg-slate-500/10 text-slate-200";
          }
          return "border-slate-600 bg-slate-800 text-slate-300";
        },
        statusDotClass() {
          const s = (this.state.status || "").toLowerCase();
          if (s === "playing") return "bg-emerald-400";
          if (s === "paused") return "bg-amber-400";
          if (s === "stopped") return "bg-slate-400";
          return "bg-slate-500";
        },
        currentTrackDisplay() {
          const idx = this.state.current_track_index || 0;
          if (!this.playlistOrder || !this.playlistOrder.length) return "";
          const p = this.playlistOrder[idx];
          if (!p) return "";
          return this.basename(p);
        },
        formattedPosition() {
          const sec = Math.floor(this.state.position_sec || 0);
          const m = Math.floor(sec / 60);
          const s = sec % 60;
          return `${m}:${s.toString().padStart(2, "0")}`;
        },
        currentDurationSec() {
          const idx = this.state.current_track_index || 0;
          if (!this.trackDurations || idx < 0 || idx >= this.trackDurations.length) {
            return null;
          }
          const d = this.trackDurations[idx];
          return typeof d === "number" && !isNaN(d) && d > 0 ? d : null;
        },
        durationLabel() {
          const d = this.currentDurationSec;
          if (!d) return "--:--";
          return this.formatDuration(d);
        },
      },
      methods: {
        
      async saveEncoderSettings() {
        const payload = {
          audio_bitrate: this.encoderSettings.audio_bitrate,
          video_bitrate: this.encoderSettings.video_bitrate,
          maxrate: this.encoderSettings.maxrate,
          bufsize: this.encoderSettings.bufsize,
          video_fps: this.encoderSettings.video_fps,
        };
        await this.send("/encoder_settings", "POST", payload);
        await this.refreshState();
      },
      removeFromPlaylist(index) {
        const current = Array.isArray(this.playlistOrder)
          ? this.playlistOrder.slice()
          : [];
        if (index < 0 || index >= current.length) return;
        current.splice(index, 1);
        this.playlistOrder = current;
        this.savePlaylistOrder();
      },
      async deleteAudioFile(f) {
        await this.send("/files/audio/delete", "POST", { path: f.path });
        if (Array.isArray(this.playlistOrder)) {
          this.playlistOrder = this.playlistOrder.filter((p) => p !== f.path);
          await this.savePlaylistOrder();
        }
        await this.refreshFiles();
      },
      async deleteVideoFile(f) {
        await this.send("/files/video/delete", "POST", { path: f.path });
        if (this.video === f.path) {
          this.video = "";
        }
        await this.refreshFiles();
      },
basename(p) {
          if (!p) return "";
          const parts = p.split(/[/\\]/);
          return parts[parts.length - 1];
        },
        dirnameShort(p) {
          if (!p) return "";
          const parts = p.split(/[/\\]/);
          if (parts.length <= 1) return "";
          return parts.slice(0, -1).join("/") || "";
        },
        formatDuration(sec) {
          if (!sec || isNaN(sec) || sec <= 0) return "--:--";
          sec = Math.floor(sec);
          const m = Math.floor(sec / 60);
          const s = sec % 60;
          return `${m}:${s.toString().padStart(2, "0")}`;
        },
        apiHeaders(json = true) {
          const h = {};
          if (json) h["Content-Type"] = "application/json";
          if (this.token) h["Authorization"] = "Bearer " + this.token;
          return h;
        },
        saveToken(t) {
          this.token = t || "";
          if (t) {
            localStorage.setItem("musicbot_session_token", t);
          } else {
            localStorage.removeItem("musicbot_session_token");
          }
        },
        async login() {
          this.loginStatus = "";
          this.loginStatusError = false;
          if (!this.loginForm.username || !this.loginForm.password) {
            this.loginStatus = "Enter username and password.";
            this.loginStatusError = true;
            return;
          }
          try {
            const res = await fetch("/login", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(this.loginForm),
            });
            if (!res.ok) {
              const txt = await res.text();
              this.loginStatus = "Login failed: " + txt;
              this.loginStatusError = true;
              this.saveToken("");
              this.state = {};
              return;
            }
            const data = await res.json();
            this.saveToken(data.token);
            this.loginStatus = "Logged in.";
            this.loginStatusError = false;
            this.loginForm.password = "";
            this.startPolling();
            this.refreshState();
            this.refreshLogs();
            this.refreshFiles();
          } catch (e) {
            this.loginStatus = "Login error: " + e;
            this.loginStatusError = true;
          }
        },
        async logout() {
          if (this.token) {
            try {
              await fetch("/logout", {
                method: "POST",
                headers: this.apiHeaders(false),
              });
            } catch (e) {
              console.warn("logout error", e);
            }
          }
          this.saveToken("");
          this.state = {};
          this.logs = [];
          this.playlistOrder = [];
          this.trackDurations = [];
          if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
          }
        },
        async send(path, method = "POST", body = null) {
          if (!this.token) {
            alert("You must login first.");
            return;
          }
          const opt = {
            method,
            headers: this.apiHeaders(!!body),
          };
          if (body) opt.body = JSON.stringify(body);
          try {
            const res = await fetch(path, opt);
            if (!res.ok) {
              const msg = await res.text();
              alert("Request failed: " + res.status + " " + msg);
            }
          } catch (e) {
            alert("Network error: " + e);
          }
          this.refreshState();
        },
        async refreshState() {
          if (!this.token) return;
          try {
            const res = await fetch("/state", {
              headers: this.apiHeaders(false),
            });
            if (!res.ok) {
              this.state = { error: "HTTP " + res.status };
              return;
            }
            const data = await res.json();
            this.state = data || {};
            // hydrate UI fields from state, but don't clobber what user is typing
            if (!this.editing.rtmp && data.rtmp_url) {
              this.rtmp = data.rtmp_url;
            }
            if (!this.editing.ffmpeg && data.ffmpeg_path) {
              this.ffmpegPath = data.ffmpeg_path;
            }
            if (!this.editing.overlay && data.overlay_text !== undefined) {
              this.overlay = data.overlay_text || "";
            }
            this.video = data.video_file || this.video;

                        // encoder settings
            if (data.audio_bitrate) {
              this.encoderSettings.audio_bitrate = data.audio_bitrate;
            }
            if (data.video_bitrate) {
              this.encoderSettings.video_bitrate = data.video_bitrate;
            }
            if (data.maxrate) {
              this.encoderSettings.maxrate = data.maxrate;
            }
            if (data.bufsize) {
              this.encoderSettings.bufsize = data.bufsize;
            }
            if (typeof data.video_fps === "number") {
              this.encoderSettings.video_fps = data.video_fps;
            }

            // playlist & durations
            if (Array.isArray(data.playlist)) {
              this.playlistOrder = data.playlist.slice();
            } else {
              this.playlistOrder = [];
            }
            if (Array.isArray(data.track_durations)) {
              this.trackDurations = data.track_durations.slice();
            } else {
              this.trackDurations = [];
            }

            // time bar max based on current track duration if known
            const d = this.currentDurationSec;
            this.timeBarMax = d && d > 0 ? Math.floor(d) : 0;

            // keep slider in sync with current position (per-track)
            if (!this.seeking) {
              this.seekSlider = Math.max(0, Math.floor(data.position_sec || 0));
            }
            // if we know track length, don't let slider go past it
            if (this.timeBarMax && this.seekSlider > this.timeBarMax) {
              this.seekSlider = this.timeBarMax;
            }
          } catch (e) {
            this.state = { error: String(e) };
          }
        },
        async refreshLogs() {
          if (!this.token) return;
          try {
            const res = await fetch("/logs?limit=200", {
              headers: this.apiHeaders(false),
            });
            if (!res.ok) {
              return;
            }
            const data = await res.json();
            this.logs = data.lines || [];
          } catch (e) {
            console.warn("log fetch error", e);
          }
        },
        async refreshFiles() {
          if (!this.token) return;
          try {
            const [ra, rv] = await Promise.all([
              fetch("/files/audio", { headers: this.apiHeaders(false) }),
              fetch("/files/video", { headers: this.apiHeaders(false) }),
            ]);
            if (ra.ok) {
              const da = await ra.json();
              this.audioFilesList = da.files || [];
            }
            if (rv.ok) {
              const dv = await rv.json();
              this.videoFilesList = dv.files || [];
            }
          } catch (e) {
            console.warn("file list error", e);
          }
        },
        setRTMP() {
          if (!this.rtmp) return;
          this.send("/rtmp", "POST", { url: this.rtmp });
        },
        setFFMPEG() {
          if (!this.ffmpegPath) return;
          this.send("/ffmpeg", "POST", { path: this.ffmpegPath });
        },
        setOverlay() {
          this.send("/overlay", "POST", { text: this.overlay || "" });
        },
        seekPrompt() {
          const sec = prompt("Seek to seconds from start of track:");
          if (sec === null) return;
          const v = parseFloat(sec);
          if (isNaN(v)) return;
          this.send("/seek", "POST", { seconds: v });
        },
        commitSeek() {
          this.seeking = false;
          const v = Number(this.seekSlider) || 0;
          this.send("/seek", "POST", { seconds: v });
        },
        // Playlist: save order to server
        savePlaylistOrder() {
          const files = this.playlistOrder.slice();
          this.send("/playlist/order", "POST", { files });
        },
        // Append from file manager
        appendToPlaylist(path) {
          const current = this.playlistOrder ? this.playlistOrder.slice() : [];
          current.push(path);
          this.playlistOrder = current;
          this.savePlaylistOrder();
        },
        setVideoFromFile(path) {
          this.video = path;
          this.send("/video", "POST", { path: this.video });
        },
        // Drag & drop handlers
        onDragStart(idx) {
          this.dragIndex = idx;
        },
        onDrop(idx) {
          if (this.dragIndex === null || this.dragIndex === idx) return;
          const arr = this.playlistOrder.slice();
          const [moved] = arr.splice(this.dragIndex, 1);
          arr.splice(idx, 0, moved);
          this.playlistOrder = arr;
          this.dragIndex = null;
        },
        playIndex(idx) {
          this.send("/play_index", "POST", { index: idx });
        },
        // Uploads
        async uploadAudioFiles() {
          if (!this.token) {
            alert("Login first.");
            return;
          }
          const input = this.$refs.audioFiles;
          if (!input || !input.files || !input.files.length) {
            alert("Select one or more audio files first.");
            return;
          }
          const addedPaths = [];
          for (const file of input.files) {
            const form = new FormData();
            form.append("file", file);
            try {
              const res = await fetch("/upload/audio", {
                method: "POST",
                headers: { Authorization: "Bearer " + this.token },
                body: form,
              });
              if (!res.ok) {
                const msg = await res.text();
                alert("Upload failed for " + file.name + ": " + msg);
                continue;
              }
              const data = await res.json();
              if (data.path) {
                addedPaths.push(data.path);
              }
            } catch (e) {
              alert("Upload error for " + file.name + ": " + e);
            }
          }
          if (addedPaths.length) {
            const current = this.playlistOrder ? this.playlistOrder.slice() : [];
            this.playlistOrder = [...current, ...addedPaths];
            this.savePlaylistOrder();
            this.refreshFiles();
          }
          input.value = "";
        },
        async uploadVideoFile() {
          if (!this.token) {
            alert("Login first.");
            return;
          }
          const input = this.$refs.videoFile;
          if (!input || !input.files || !input.files.length) {
            alert("Select a video file first.");
            return;
          }
          const file = input.files[0];
          const form = new FormData();
          form.append("file", file);
          try {
            const res = await fetch("/upload/video", {
              method: "POST",
              headers: { Authorization: "Bearer " + this.token },
              body: form,
            });
            if (!res.ok) {
              const msg = await res.text();
              alert("Upload failed: " + msg);
              return;
            }
            const data = await res.json();
            if (data.path) {
              this.video = data.path;
              this.send("/video", "POST", { path: this.video });
              this.refreshFiles();
            }
          } catch (e) {
            alert("Upload error: " + e);
          }
          input.value = "";
        },
        startPolling() {
          if (this._pollTimer) return;
          this._pollTimer = setInterval(() => {
            if (!this.token) return;
            this.refreshState();
            this.refreshLogs();
          }, 1000);
        },
      },
      mounted() {
        if (this.token) {
          this.startPolling();
          this.refreshState();
          this.refreshLogs();
          this.refreshFiles();
        }
      },
    }).mount("#app");
