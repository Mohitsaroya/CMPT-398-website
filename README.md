# Fire Detection Demo

A real-time fire-detection demo. A Python bridge reads frames from a webcam or a
network camera deck, runs the included YOLO model, and sends annotated-frame
data to a Node.js server. The React dashboard displays the latest frame and
detection status.

## Requirements

- Node.js and npm
- Python 3.9 or newer
- A webcam, or a compatible camera deck reachable over the network

## Setup

Install the Node.js dependencies:

```powershell
cd server
npm install
```

Install the Python dependencies:

```powershell
python -m pip install opencv-python numpy requests ultralytics
```

The model weights are provided in `server/best.pt`.

## Run with a webcam

From the `server` directory, configure the bridge to send frames to the local
server and start the API, Python bridge, and Vite development server:

```powershell
$env:SERVER = "http://localhost:3000/frame"
npm run dev
```

Open the Vite URL printed in the terminal (usually
`http://localhost:5173`). Allow camera access if prompted by your system.

## Run with a camera deck

The bridge looks for the deck address in `server/deck_ip.txt`. Alternatively,
pass the address as the second argument to `bridge.py`:

```powershell
python -u bridge.py deck 192.168.1.100
```

To run the backend and deck bridge together:

```powershell
$env:SERVER = "http://localhost:3000/frame"
npm run demo
```

To view the dashboard while using `npm run demo`, start the Vite server in a
second terminal:

```powershell
cd fire-ui
npm install
npm run dev
```

## Build

From the `server` directory, build the dashboard for the Node.js server to
serve from `fire-ui/dist`:

```powershell
npm run build
```

Then start the server with `node server.js`. It listens on port `3000` by
default; set `PORT` to use another port.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `SERVER` | Frame endpoint used by the Python bridge | `https://cmpt-398-website.onrender.com/frame` |
| `FRAME_TOKEN` | Optional shared token sent by the bridge and checked by the server | Unset |
| `PORT` | HTTP and WebSocket server port | `3000` |

When `FRAME_TOKEN` is set, set it to the same value in the environments running
the bridge and Node.js server. For local development, the commands above set
`SERVER` to the local frame endpoint so frames appear in the local dashboard.

## Project layout

- `fire-ui/` — React dashboard and Vite configuration
- `server/server.js` — Express frame endpoint and WebSocket broadcaster
- `server/bridge.py` — Camera input, YOLO inference, and frame uploader
- `server/best.pt` — YOLO model weights
- `server/deck_ip.txt` — Optional cached camera deck IP address
