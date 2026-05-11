# DistributedAI — Chat Client

A React + Vite frontend that streams token-by-token inference responses from an Express backend over HTTP chunked transfer (NDJSON).

---

## Features

- Real-time token streaming via `fetch` + `ReadableStream`
- NDJSON protocol (`application/x-ndjson`) — no WebSockets required
- Abort/stop mid-stream
- Auto-scrolling chat UI with per-message token counts
- Dockerized production build served by nginx

---

## Project Structure

```
client_side/
├── src/
│   ├── components/
│   │   ├── Chat.jsx          # Main chat component & streaming logic
│   │   ├── Chat.module.css
│   │   ├── Message.jsx
│   │   └── Message.module.css
│   ├── App.jsx
│   ├── main.jsx
│   └── index.css
├── .env                      # VITE_API_URL (build-time)
├── Dockerfile                # Multi-stage build → nginx
├── nginx.conf                # SPA routing + static asset caching
├── vite.config.js
└── package.json
```

---

## Prerequisites

- [Node.js](https://nodejs.org/) v18+
- The inference server running on port `8000` (see below)

---

## Getting Started

### 1. Install dependencies

```bash
npm install
```

### 2. Configure the API URL

Edit `.env`:

```env
VITE_API_URL=http://localhost:8000
```

> **Note:** Vite inlines `VITE_*` variables at **build time**. Changing `.env` after building has no effect.

### 3. Run in development mode

```bash
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

> If the server is on a different origin you will hit CORS. Either add `cors()` middleware to the Express server or add a Vite proxy:
>
> ```js
> // vite.config.js
> server: {
>     proxy: { '/infer': 'http://localhost:8000' }
> }
> ```
> and set `VITE_API_URL=` (empty) in `.env`.

---

## Production Build (Docker)

```bash
docker build \
  --build-arg VITE_API_URL=http://<your-server-ip>:8000 \
  -t chat-client .

docker run -p 80:80 chat-client
```

The image uses a two-stage build:

| Stage | Base image | Purpose |
|-------|-----------|---------|
| `builder` | `node:20-alpine` | `npm ci` + `vite build` |
| final | `nginx:1.27-alpine` | Serve `/app/dist` as static files |

---

## Inference Server API

The client expects the following endpoint on `VITE_API_URL`:

### `POST /infer`

**Request**

```json
{ "text": "your prompt here" }
```

**Response** — `Content-Type: application/x-ndjson`, chunked

Each line is a JSON object:

```json
{ "chunk": "token", "is_final": false }
{ "chunk": "lasttoken", "is_final": true }
```

The client appends a space between tokens to reconstruct the original text.

---

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start Vite dev server (HMR) |
| `npm run build` | Production build → `dist/` |
| `npm run preview` | Preview the production build locally |

---

## Dependencies

| Package | Version | Role |
|---------|---------|------|
| `react` | ^18.3.1 | UI framework |
| `react-dom` | ^18.3.1 | DOM renderer |
| `vite` | ^5.4.0 | Build tool & dev server |
| `@vitejs/plugin-react` | ^4.3.1 | JSX transform + HMR |
