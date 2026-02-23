#!/usr/bin/env node
"use strict";

const http = require("http");

const port = Number(process.env.PORT || 3000);

const server = http.createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok" }));
    return;
  }

  res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
  res.end("Factory fallback scaffold");
});

if (require.main === module) {
  server.listen(port, () => {
    process.stdout.write(`fallback server listening on ${port}\n`);
  });
}

module.exports = server;
