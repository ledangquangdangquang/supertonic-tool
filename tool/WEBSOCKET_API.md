# Supertonic TTS — WebSocket API Guide

Integration guide for native apps, web apps, and Chrome/browser extensions.

---

## 1. Overview

The Supertonic TTS server exposes a single WebSocket endpoint that converts
text into speech audio (WAV, PCM 16-bit). Each client request produces
two messages in return: a JSON metadata frame and a binary audio frame.

| Property       | Value                         |
| -------------- | ----------------------------- |
| URL            | `ws://127.0.0.1:8765`         |
| Protocol       | Plain WebSocket (no TLS)      |
| Scope          | Localhost only (loopback)     |
| Audio format   | WAV, 16-bit PCM mono          |
| Max frame size | 10 MB                         |
| Ping interval  | 20 s (server → client)        |

> The server binds to `127.0.0.1`. It is **not** reachable from other
> machines, LAN, or through `https://` pages without a reverse proxy.

---

## 2. Message Flow

```
 Client                                    Server
   │   ──── WebSocket connect ─────────►     │
   │   ◄──── JSON { status:"connected",      │   [handshake]
   │            voices:[...], languages:[...] }
   │                                         │
   │   ──── JSON { text, lang, voice } ─►    │   [request 1]
   │   ──── JSON { text, lang, voice } ─►    │   [request 2]  ← can send
   │   ──── JSON { text, lang, voice } ─►    │   [request 3]    multiple
   │                                         │
   │         (server batches within 100ms)   │
   │                                         │
   │   ◄──── JSON { type:"audio_meta", ...}  │   [response 1]
   │   ◄──── Binary WAV bytes ─────────────  │
   │   ◄──── JSON { type:"audio_meta", ...}  │   [response 2]
   │   ◄──── Binary WAV bytes ─────────────  │
   │   ◄──── JSON { type:"audio_meta", ...}  │   [response 3]
   │   ◄──── Binary WAV bytes ─────────────  │
```

The connection stays open. You can send many requests without waiting for
responses — they are collected and processed as a batch.

---

## 3. Server → Client Messages

### 3.1 Handshake (once, on connect)

```json
{
  "status": "connected",
  "voices": ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"],
  "languages": ["en", "vi", "ko", "ja", "fr", "de", "es", "..."]
}
```

Use this to populate UI dropdowns.

### 3.2 Audio metadata (before every audio frame)

```json
{
  "type": "audio_meta",
  "text": "Hello world.",  // echo of the request text (for matching)
  "duration": 3.520,       // seconds of audio generated
  "latency_ms": 118,       // server-side inference time
  "size": 112640           // bytes of the next binary frame
}
```

The `text` field echoes the original request text so clients can match
responses to requests when multiple are in-flight.

### 3.3 Audio payload (binary)

The message immediately following `audio_meta` is a binary frame containing
a complete WAV file (header + PCM data). Treat it as a `Blob` / `ArrayBuffer`
/ `byte[]` and hand it directly to your audio decoder.

### 3.4 Error

```json
{ "type": "error", "message": "..." }
```

On error, no binary frame follows. The socket stays open, so you can retry.

---

## 4. Client → Server Messages

Send a single JSON frame per request:

```json
{
  "text":  "Hello world.",
  "lang":  "en",
  "voice": "M1",
  "speed": 1.05
}
```

| Field   | Type   | Required | Default | Notes                                              |
| ------- | ------ | -------- | ------- | -------------------------------------------------- |
| `text`  | string | yes      | —       | Empty / whitespace-only is silently ignored        |
| `lang`  | string | no       | `"en"`  | Accepts `en-US`-style; anything after `-` stripped |
| `voice` | string | no       | `"M1"`  | Case-insensitive; unknown voices fall back to `M1` |
| `speed` | number | no       | `1.05`  | Clamped to `[0.25, 4.0]` server-side — see note    |

### Inline markers supported in `text`

| Marker      | Effect                |
| ----------- | --------------------- |
| `,`         | short pause           |
| `.`         | ~0.3 s pause          |
| `<breath>`  | inhale / breath sound |
| `<laugh>`   | laughter              |
| `<sigh>`    | sigh                  |

### Note on `speed`

The server currently generates audio at a fixed internal rate; the `speed`
field is accepted and validated but does not change the generated waveform.
**Apply playback speed on the client side**:

- Web/Browser: `audioEl.playbackRate = 1.5; audioEl.preservesPitch = true;`
- Native: use your platform's audio stretch/time-scale API.

---

## 5. Examples

### 5.1 Vanilla JavaScript (web app)

```js
const ws = new WebSocket('ws://127.0.0.1:8765');
ws.binaryType = 'blob';

ws.addEventListener('open', () => {
  console.log('connected');
});

let pendingMeta = null;

ws.addEventListener('message', (ev) => {
  if (typeof ev.data === 'string') {
    const msg = JSON.parse(ev.data);
    if (msg.status === 'connected') {
      console.log('voices:', msg.voices, 'langs:', msg.languages);
    } else if (msg.type === 'audio_meta') {
      pendingMeta = msg;
    } else if (msg.type === 'error') {
      console.error('server error:', msg.message);
    }
  } else {
    // Binary WAV
    const blob = new Blob([ev.data], { type: 'audio/wav' });
    const audio = new Audio(URL.createObjectURL(blob));
    audio.playbackRate = 1.2;
    audio.preservesPitch = true;
    audio.play();
    console.log('rendered in', pendingMeta?.latency_ms, 'ms');
  }
});

// Send a request
function speak(text, lang = 'en', voice = 'M1') {
  ws.send(JSON.stringify({ text, lang, voice }));
}
```

### 5.2 Chrome Extension (Manifest V3)

**Manifest permissions.** Connecting to `ws://127.0.0.1` from an extension
does *not* require a `host_permissions` entry, but the request must originate
from a context that can reach loopback (background service worker or an
extension page — *not* a page under `file://`).

`manifest.json`:

```json
{
  "manifest_version": 3,
  "name": "Supertonic TTS Client",
  "version": "1.0.0",
  "background": { "service_worker": "background.js" },
  "permissions": ["offscreen"],
  "action": { "default_popup": "popup.html" }
}
```

`background.js` — connection manager with auto-reconnect:

```js
let ws = null;
let reconnectTimer = null;

function connect() {
  ws = new WebSocket('ws://127.0.0.1:8765');
  ws.binaryType = 'arraybuffer';

  ws.onopen  = () => console.log('[TTS] connected');
  ws.onclose = () => {
    console.warn('[TTS] disconnected — retrying in 2 s');
    reconnectTimer = setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();

  let meta = null;
  ws.onmessage = async (ev) => {
    if (typeof ev.data === 'string') {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'audio_meta') meta = msg;
      else if (msg.type === 'error') console.error(msg.message);
    } else {
      // Binary WAV — MV3 service workers can't play audio directly,
      // so forward to an offscreen document.
      await ensureOffscreen();
      chrome.runtime.sendMessage({
        target: 'offscreen',
        type: 'play-wav',
        buffer: Array.from(new Uint8Array(ev.data)), // transferable
        meta,
      });
    }
  };
}

// Keep the service worker alive while a request is in-flight
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'speak') {
    if (!ws || ws.readyState !== 1) connect();
    const trySend = () => ws.send(JSON.stringify({
      text: msg.text, lang: msg.lang ?? 'en', voice: msg.voice ?? 'M1',
    }));
    ws.readyState === 1 ? trySend() : ws.addEventListener('open', trySend, { once: true });
  }
});

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument?.();
  if (has) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['AUDIO_PLAYBACK'],
    justification: 'Play synthesized TTS audio',
  });
}

connect();
```

`offscreen.html`:

```html
<!doctype html>
<script src="offscreen.js"></script>
```

`offscreen.js`:

```js
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== 'offscreen' || msg.type !== 'play-wav') return;
  const bytes = new Uint8Array(msg.buffer);
  const blob = new Blob([bytes], { type: 'audio/wav' });
  const audio = new Audio(URL.createObjectURL(blob));
  audio.playbackRate = 1.2;
  audio.preservesPitch = true;
  audio.play();
});
```

`popup.html` — trigger from the UI:

```html
<button id="speak">Speak</button>
<script>
  document.getElementById('speak').onclick = () => {
    chrome.runtime.sendMessage({ type: 'speak', text: 'Hello from the extension.' });
  };
</script>
```

**Why the offscreen document?** MV3 service workers have no DOM and cannot
instantiate `Audio()` or `AudioContext`. The offscreen API is the official
Chrome workaround for audio playback from an extension background.

### 5.3 Node.js (desktop / CLI)

```js
import WebSocket from 'ws';
import { writeFileSync } from 'node:fs';

const ws = new WebSocket('ws://127.0.0.1:8765');
let meta = null;

ws.on('open', () => {
  ws.send(JSON.stringify({ text: 'Hello from Node.', voice: 'F1' }));
});

ws.on('message', (data, isBinary) => {
  if (!isBinary) {
    const msg = JSON.parse(data.toString());
    if (msg.type === 'audio_meta') meta = msg;
  } else {
    writeFileSync('out.wav', data);
    console.log('saved out.wav', meta);
    ws.close();
  }
});
```

### 5.4 Python client

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8765") as ws:
        await ws.recv()  # discard handshake
        await ws.send(json.dumps({"text": "Hello from Python.", "voice": "M2"}))
        meta = json.loads(await ws.recv())
        audio = await ws.recv()  # bytes
        with open("out.wav", "wb") as f:
            f.write(audio)
        print(meta)

asyncio.run(main())
```

---

## 6. Operational Notes

### Batching & GPU serialization

The server uses a **batch inference** architecture:

1. Incoming requests are collected into a batch queue.
2. After a 100 ms collection window, all queued requests are processed in
   a single GPU inference call (grouped by voice).
3. A global GPU lock ensures only one batch runs at a time — preventing
   DirectML command queue overflow.

This means:
- Sending 6 requests at once results in **1 batch inference** (~1–2 s total)
  instead of 6 sequential inferences (~6–12 s).
- Responses arrive in batch order, not necessarily request order. Use the
  `text` field in `audio_meta` to match responses to requests.
- If the client disconnects before results are ready, the server skips
  sending to that client (no crash).

### Connection lifecycle
- Server pings every 20 s; idle connections are kept alive.
- Multiple requests can be sent without waiting for responses — they are
  batched and processed together.
- On inference failure the server retries up to 3× internally before
  sending an `error` frame.
- Client disconnect mid-inference is handled gracefully; the server
  continues serving other clients.

### Recommended client patterns
- **Always reconnect on close.** Use exponential backoff (e.g. 1 s → 2 s → 5 s).
- **Queue requests** if the socket is not yet `OPEN`.
- **Buffer `audio_meta`** until the next binary frame arrives — they are
  strictly paired in order.
- Use `binaryType = 'arraybuffer'` when you need to inspect bytes;
  `'blob'` is slightly faster for direct `<audio>` playback.

### Security
- The server is **unauthenticated** and binds to loopback only. Do not
  expose it to the network without adding auth + TLS (e.g. via a reverse
  proxy such as Caddy or nginx with basic auth).
- From `https://` origins, browsers block `ws://` connections. Either:
  - host your page on `http://localhost`, or
  - put a TLS-terminating proxy (`wss://`) in front of the server.

### Troubleshooting

| Symptom                             | Likely cause                                   |
| ----------------------------------- | ---------------------------------------------- |
| `WebSocket is closed before ...`    | Server not running / port 8765 taken           |
| Binary frame never arrives          | You sent an empty/whitespace `text`            |
| Wrong voice played                  | Typo in `voice` → silently falls back to `M1`  |
| Garbled / no audio in extension     | Missing offscreen document in MV3              |
| Mixed-content error in browser      | Page is `https://`, server is `ws://` — use a proxy |
| `887A0006 GPU will not respond`     | onnxruntime package conflict — ensure only `onnxruntime-directml` is installed (not both `-gpu` and `-directml`) |
| Server dies on video seek           | Client sending too many requests before disconnect — server now handles this via batching + graceful disconnect |

---

## 7. Quick Reference

```text
CONNECT     ws://127.0.0.1:8765
RECV JSON   { status:"connected", voices, languages }

SEND JSON   { text, lang?, voice?, speed? }     ← can send multiple
SEND JSON   { text, lang?, voice?, speed? }       without waiting

(server batches within 100ms, processes in 1 GPU call)

RECV JSON   { type:"audio_meta", text, duration, latency_ms, size }
RECV BIN    <WAV bytes, 16-bit PCM mono>
  ... (one pair per request)

ON ERROR    RECV JSON { type:"error", message }
```
