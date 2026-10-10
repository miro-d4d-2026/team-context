#!/usr/bin/env node
// The bridge owns state. A failed worker can be restarted and retried safely.
import readline from 'node:readline';
import { createDemoState, evaluate } from './model_bundle/rules/engine.mjs';
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  try {
    const message = JSON.parse(line);
    if (message.action === 'ping') {
      process.stdout.write(JSON.stringify({ ok: true, protocol: 'genguard.rule-worker/1' }) + '\n');
      continue;
    }
    if (message.action !== 'evaluate' || !message.snapshot) throw new Error('Unsupported worker request');
    const nextState = message.state ? structuredClone(message.state) : createDemoState();
    // No old statistical demo baseline: the trained Python model handles patterns.
    const result = evaluate(message.snapshot, nextState);
    process.stdout.write(JSON.stringify({ ok: true, result, next_state: nextState }) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(error.message) }) + '\n');
  }
}
