// Read only the requested frame; retain at most two chunks per dynamic field.
export function createChunkFrameLoader(specs, cells, fetchBuffer) {
  const bytes = {uint8:1, "float32-le":4, "float64-le":8};
  const cache = new Map();
  const limit = Object.keys(specs).length * 2;
  return {
    async load(frame) {
      if (!Number.isInteger(frame) || frame < 0) throw new Error("帧编号无效");
      const entries = await Promise.all(Object.entries(specs).map(async ([name,spec]) => {
        const size = bytes[spec.dtype];
        const chunk = spec.chunks?.find(c => frame >= c.frame_start && frame < c.frame_start + c.frame_count);
        if (!size || !chunk) throw new Error(`帧数据缺失：${name}`);
        let pending = cache.get(chunk.path);
        if (pending) { cache.delete(chunk.path); cache.set(chunk.path,pending); }
        else {
          pending = fetchBuffer(chunk.path).catch(error => { cache.delete(chunk.path); throw error; });
          cache.set(chunk.path,pending);
        }
        const buffer = await pending;
        if (buffer.byteLength !== chunk.byte_length || buffer.byteLength !== chunk.frame_count * cells * size) {
          throw new Error(`分块长度不正确：${chunk.path}`);
        }
        const start = (frame - chunk.frame_start) * cells * size;
        return [name,buffer.slice(start,start + cells * size)];
      }));
      while (cache.size > limit) cache.delete(cache.keys().next().value);
      return Object.fromEntries(entries);
    },
    get cachedChunkCount() { return cache.size; },
  };
}
