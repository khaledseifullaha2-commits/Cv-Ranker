/**
 * typed-array-compat.ts — small, guarded compatibility shim.
 *
 * pdf.js 5.7/6.x uses the new TC39 typed-array helpers:
 *
 *   * `Uint8Array.prototype.toHex()`  — the WORKER calls it for every document
 *     (the `fingerprints` getter computes an MD5 and hex-encodes it), so on an
 *     engine without it *every* PDF fails with
 *     "a.toHex is not a function";
 *   * `TypedArray.prototype.toBase64()` / `Uint8Array.fromBase64()` — used on
 *     the font and signature paths.
 *
 * These landed only in very recent engines (Chrome 140+, Safari 18.4+), which
 * would shut out plenty of ordinary office browsers. So define them when they
 * are missing and never touch a native implementation.
 *
 * The shim is imported by the app AND by `pdf-worker.ts`: the worker is a
 * separate JS realm with its own prototypes, so it needs its own copy.
 */

type HexApi = {
  toHex?: () => string;
  fromHex?: (value: string) => Uint8Array;
  toBase64?: (options?: { alphabet?: string; omitPadding?: boolean }) => string;
  fromBase64?: (value: string, options?: { alphabet?: string; lastChunkHandling?: string }) => Uint8Array;
};

const BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function toHex(this: Uint8Array): string {
  let out = "";
  for (let i = 0; i < this.length; i++) {
    out += this[i].toString(16).padStart(2, "0");
  }
  return out;
}

function fromHex(value: string): Uint8Array {
  const text = String(value);
  if (text.length % 2 !== 0 || /[^0-9a-fA-F]/.test(text)) {
    throw new SyntaxError("Invalid hex string");
  }
  const out = new Uint8Array(text.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = Number.parseInt(text.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

function toBase64(this: Uint8Array): string {
  let out = "";
  for (let i = 0; i < this.length; i += 3) {
    const b0 = this[i];
    const b1 = this[i + 1];
    const b2 = this[i + 2];
    out += BASE64_ALPHABET[b0 >> 2];
    out += BASE64_ALPHABET[((b0 & 0x03) << 4) | ((b1 ?? 0) >> 4)];
    out += b1 === undefined ? "=" : BASE64_ALPHABET[((b1 & 0x0f) << 2) | ((b2 ?? 0) >> 6)];
    out += b2 === undefined ? "=" : BASE64_ALPHABET[b2 & 0x3f];
  }
  return out;
}

function fromBase64(value: string, options?: { alphabet?: string }): Uint8Array {
  let text = String(value).replace(/[\t\n\f\r ]/g, "");
  if (options?.alphabet === "base64url") {
    text = text.replace(/-/g, "+").replace(/_/g, "/");
  }
  const padIndex = text.indexOf("=");
  if (padIndex >= 0) text = text.slice(0, padIndex);
  const bytes: number[] = [];
  let buffer = 0;
  let bits = 0;
  for (const char of text) {
    const index = BASE64_ALPHABET.indexOf(char);
    if (index < 0) throw new SyntaxError("Invalid base64 string");
    buffer = (buffer << 6) | index;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >> bits) & 0xff);
    }
  }
  return Uint8Array.from(bytes);
}

/** Define only what the engine is missing; a native method always wins. */
export function installTypedArrayCompat(): void {
  const proto = Uint8Array.prototype as unknown as HexApi;
  const ctor = Uint8Array as unknown as HexApi;

  const define = (target: object, name: string, value: unknown) => {
    Object.defineProperty(target, name, {
      value,
      writable: true,
      enumerable: false,
      configurable: true,
    });
  };

  if (typeof proto.toHex !== "function") define(Uint8Array.prototype, "toHex", toHex);
  if (typeof proto.toBase64 !== "function") define(Uint8Array.prototype, "toBase64", toBase64);
  if (typeof ctor.fromHex !== "function") define(Uint8Array, "fromHex", fromHex);
  if (typeof ctor.fromBase64 !== "function") define(Uint8Array, "fromBase64", fromBase64);
}

installTypedArrayCompat();
