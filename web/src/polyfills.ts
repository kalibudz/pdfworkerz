/**
 * `Map`/`WeakMap.prototype.getOrInsertComputed` (TC39 "Upsert") is used
 * internally by pdfjs-dist. Confirmed empirically: current, fully-patched
 * pdfjs-dist releases (needed for a real CVE fix -- see web/README.md)
 * throw `getOrInsertComputed is not a function` on an engine that predates
 * this proposal landing, breaking page rendering outright. This fills the
 * gap on such an engine only -- it never overrides a native implementation,
 * so it's a no-op on any current, real-world browser.
 */

type Upsertable<K, V> = { getOrInsertComputed?: (key: K, callback: (key: K) => V) => V };

function installGetOrInsertComputed<K, V>(proto: Upsertable<K, V> & { has(key: K): boolean }): void {
  if (typeof proto.getOrInsertComputed === "function") {
    return;
  }
  Object.defineProperty(proto, "getOrInsertComputed", {
    configurable: true,
    writable: true,
    value(this: Map<K, V> | WeakMap<K & object, V>, key: K, callback: (key: K) => V): V {
      const self = this as unknown as { has(key: K): boolean; get(key: K): V | undefined; set(key: K, value: V): unknown };
      if (self.has(key)) {
        return self.get(key) as V;
      }
      const computed = callback(key);
      self.set(key, computed);
      return computed;
    },
  });
}

installGetOrInsertComputed(Map.prototype as unknown as Upsertable<unknown, unknown> & { has(key: unknown): boolean });
installGetOrInsertComputed(
  WeakMap.prototype as unknown as Upsertable<unknown, unknown> & { has(key: unknown): boolean },
);
