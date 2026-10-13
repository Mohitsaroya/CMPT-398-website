const express = require('express');
const http = require('http');
const path = require('path');
const { WebSocketServer } = require('ws');

const app = express();
app.use(express.json({ limit: '5mb' }));
app.use(express.static(path.join(__dirname, '../fire-ui/dist')));

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

const TOKEN = process.env.FRAME_TOKEN;

let seq = 0;
let latest = null;
let latestFrameSeq = 0;

app.post('/frame', (req, res) => {
  if (TOKEN && req.get('x-token') !== TOKEN) return res.sendStatus(401);

  const body = req.body;
  if (Number.isSafeInteger(body.frameSeq)) {
    if (body.frameSeq <= latestFrameSeq) return res.sendStatus(202);
    latestFrameSeq = body.frameSeq;
  }
  body.seq = ++seq;
  body.serverTs = Date.now();
  latest = JSON.stringify(body);

  wss.clients.forEach(c => {
    if (c.readyState !== 1) return;
    if (c.bufferedAmount > 1_000_000) return;
    c.send(latest);
  });

  res.sendStatus(204);
  if (seq % 100 === 0) console.log(`frames received: ${seq}, clients: ${wss.clients.size}`);
});

wss.on('connection', ws => {
  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });
  if (latest) ws.send(latest); 
});

// Heartbeat: keeps Render's proxy from closing idle sockets and
// removes dead ones (e.g. a phone that locked its screen).
setInterval(() => {
  wss.clients.forEach(ws => {
    if (!ws.isAlive) return ws.terminate();
    ws.isAlive = false;
    ws.ping();
  });
}, 25000);

server.listen(process.env.PORT || 3000, () => console.log('listening'));