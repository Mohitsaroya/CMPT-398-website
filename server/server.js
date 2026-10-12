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

app.post('/frame', (req, res) => {
  if (TOKEN && req.get('x-token') !== TOKEN) return res.sendStatus(401);
  const msg = JSON.stringify(req.body);
  wss.clients.forEach(c => c.readyState === 1 && c.send(msg));
  res.sendStatus(204);
  console.log('frame received');
});

server.listen(process.env.PORT || 3000, () => console.log('listening'));