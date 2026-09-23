const {test} = require('node:test');
const assert = require('node:assert/strict');
const Portable = require('../src/investment_lab/web/static/portable-data.js');

async function gzip(text) {
  const stream = new Blob([text]).stream().pipeThrough(new CompressionStream('gzip'));
  return new Response(stream).blob();
}

async function gzipBytes(bytes) {
  const stream = new Blob([new Uint8Array(bytes)]).stream().pipeThrough(new CompressionStream('gzip'));
  return new Response(stream).blob();
}

test('gzip package parses valid UTF-8 JSON and respects compressed/decompressed limits', async () => {
  const value = {format: 'investment-lab-snapshot', rows: [1, 2, 3]};
  const file = await gzip(JSON.stringify(value));
  assert.deepEqual(await Portable.readGzipJson(file), value);
  await assert.rejects(Portable.readGzipJson(file, {maxCompressedBytes: file.size - 1}), /压缩包超过允许大小/);
  await assert.rejects(Portable.readGzipJson(file, {maxUncompressedBytes: 8}), /超过安全上限/);
});

test('invalid gzip or invalid UTF-8 JSON fails without returning partial content', async () => {
  await assert.rejects(Portable.readGzipJson(new Blob(['not gzip'])), /gzip/i);
  const invalidJson = await gzip('{');
  await assert.rejects(Portable.readGzipJson(invalidJson), /有效的 UTF-8 JSON/);
  const invalidUtf8 = await gzipBytes([0xFF, 0xFE]);
  await assert.rejects(Portable.readGzipJson(invalidUtf8), /有效的 UTF-8 JSON/);
});
