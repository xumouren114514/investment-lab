(function (root) {
  'use strict';

  const MAX_COMPRESSED_BYTES = 250 * 1024 * 1024;
  const MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024;

  async function readGzipText(file, limits = {}) {
    if (!file || typeof file.stream !== 'function') throw new Error('请先选择 gzip 数据包。');
    const maxCompressedBytes = Math.min(MAX_COMPRESSED_BYTES, limits.maxCompressedBytes ?? MAX_COMPRESSED_BYTES);
    const maxUncompressedBytes = Math.min(MAX_UNCOMPRESSED_BYTES, limits.maxUncompressedBytes ?? MAX_UNCOMPRESSED_BYTES);
    if (file.size > maxCompressedBytes) throw new Error('压缩包超过允许大小，请按标的拆分后再导入。');
    if (typeof DecompressionStream !== 'function') throw new Error('当前浏览器不支持 gzip 解压，请使用较新的浏览器后重试。');

    let reader;
    try {
      reader = file.stream().pipeThrough(new DecompressionStream('gzip')).getReader();
    } catch (_) {
      throw new Error('gzip 解压失败，文件可能损坏或格式不符。');
    }
    const chunks = [];
    let length = 0;
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > maxUncompressedBytes) {
          try { await reader.cancel(); } catch (_) { /* Preserve the size-limit error. */ }
          throw new Error('解压后的数据包超过安全上限，请按标的拆分后再导入。');
        }
        chunks.push(value);
      }
    } catch (error) {
      if (error.message === '解压后的数据包超过安全上限，请按标的拆分后再导入。') throw error;
      throw new Error('gzip 解压失败，文件可能损坏或格式不符。');
    } finally {
      reader.releaseLock();
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    try { return new TextDecoder('utf-8', {fatal: true}).decode(bytes); }
    catch (error) { throw new Error(`数据包不是有效的 UTF-8 JSON：${error.message}`); }
  }

  async function readGzipJson(file, limits = {}) {
    const text = await readGzipText(file, limits);
    try { return JSON.parse(text); }
    catch (error) { throw new Error(`数据包不是有效的 UTF-8 JSON：${error.message}`); }
  }

  const api = {MAX_COMPRESSED_BYTES, MAX_UNCOMPRESSED_BYTES, readGzipText, readGzipJson};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PortableDataPackage = api;
})(globalThis);
