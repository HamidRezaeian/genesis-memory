/**
 * Client-side port of genesis_memory/core/privacy_shield.py — same detectors,
 * same 4.0 bits/char Shannon threshold — so the sandbox on the landing page
 * behaves exactly like the shield that guards the SQLite store.
 */
export const ENTROPY_THRESHOLD_BITS = 4.0
export const ENTROPY_MIN_LENGTH = 20
export const REDACTED = '[REDACTED_API_KEY]'

const TOKEN_RE = /[A-Za-z0-9_\-+/=.]{20,}/g
const HEX_RE = /^[0-9a-fA-F]+$/
const MIXED_RE = /(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/

export const STRUCTURAL = [
  ['pem_block', /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g],
  ['pem_unclosed', /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*/g],
  ['openai', /\b(sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,})\b/g],
  ['google', /\b(AIza[0-9A-Za-z\-_]{30,})\b/g],
  ['github', /\b(gh[pousr]_[A-Za-z0-9_]{30,})\b/g],
  ['github_pat', /\b(github_pat_[A-Za-z0-9_]{60,})\b/g],
  ['aws_access', /\b((?:AKIA|ASIA)[0-9A-Z]{16})\b/g],
  ['slack', /\b(xox[abprs]-[A-Za-z0-9-]{10,})\b/g],
  ['stripe', /\b([sr]k_(?:live|test)_[A-Za-z0-9]{16,})\b/g],
  ['sendgrid', /\b(SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})\b/g],
  ['npm', /\b(npm_[A-Za-z0-9]{30,})\b/g],
  ['huggingface', /\b(hf_[A-Za-z0-9]{30,})\b/g],
  ['jwt', /\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b/g],
  ['bearer', /\bBearer\s+[A-Za-z0-9_\-.=]{25,}\b/g],
  ['basic_auth_url', /(?<=:\/\/)[^\s:/@]{1,64}:[^\s/@]{4,}(?=@)/g],
  ['assignment', /\b([A-Z0-9_\-.]*(?:api[_-]?key|secret|token|passwd|password|private[_-]?key|access[_-]?key|client[_-]?secret|auth)[A-Z0-9_\-.]*)(\s*[:=]\s*["']?)([^\s"',;]{8,})/gi],
]

export function shannonEntropy(s) {
  if (!s) return 0
  const counts = new Map()
  for (const ch of s) counts.set(ch, (counts.get(ch) || 0) + 1)
  const n = s.length
  let h = 0
  for (const c of counts.values()) { const p = c / n; h -= p * Math.log2(p) }
  return h
}

const SEG_OK = /^[A-Za-z0-9_.\-~]+$/
function wordySegment(seg) {
  if (seg.length < 2 || !SEG_OK.test(seg)) return false
  const digits = (seg.match(/\d/g) || []).length
  if (digits / seg.length > 0.3) return false
  let flips = 0, prev = null
  for (const ch of seg) { if (/[A-Za-z]/.test(ch)) { const cur = ch === ch.toUpperCase(); if (prev !== null && cur !== prev) flips++; prev = cur } }
  return flips <= 1
}

function looksLikeProseOrPath(tok) {
  const low = tok.toLowerCase()
  if (low.startsWith('http://') || low.startsWith('https://') || low.startsWith('www.')) return true
  if ((tok.match(/\//g) || []).length >= 2 && !tok.includes('+') && !tok.includes('=')) {
    const segs = tok.split('/').filter(Boolean)
    if (segs.length && segs.filter(wordySegment).length / segs.length >= 0.6) return true
  }
  if ((tok.match(/\./g) || []).length >= 3 && tok.split('.').every(s => !s || wordySegment(s))) return true
  if ((tok.match(/-/g) || []).length >= 4 && /^[A-Za-z]+$/.test(tok.replace(/-/g, ''))) return true
  if ((tok.match(/_/g) || []).length >= 3 && /^[A-Za-z]+$/.test(tok.replace(/_/g, ''))) return true
  return false
}

export function isHighEntropyToken(tok) {
  if (tok.length < ENTROPY_MIN_LENGTH || looksLikeProseOrPath(tok)) return false
  const core = tok.replace(/^[=.\-_]+|[=.\-_]+$/g, '')
  if (core.length < ENTROPY_MIN_LENGTH) return false
  if (HEX_RE.test(core)) return false // git SHAs / digests are legitimate memory
  const ent = shannonEntropy(core)
  if (ent >= ENTROPY_THRESHOLD_BITS + 0.5) return true
  return ent >= ENTROPY_THRESHOLD_BITS && MIXED_RE.test(core)
}

export function analyze(text) {
  const findings = []
  let out = text
  const byDetector = {}
  for (const [name, re] of STRUCTURAL) {
    re.lastIndex = 0
    if (name === 'assignment') {
      out = out.replace(re, (m, k, sep) => { byDetector[name] = (byDetector[name] || 0) + 1; findings.push({ detector: name, len: m.length }); return `${k}${sep}${REDACTED}` })
    } else {
      out = out.replace(re, (m) => { byDetector[name] = (byDetector[name] || 0) + 1; findings.push({ detector: name, len: m.length }); return REDACTED })
    }
  }
  const tokens = []
  let m
  const tre = /[A-Za-z0-9_\-+/=.]{8,}/g
  while ((m = tre.exec(text))) tokens.push({ tok: m[0], bits: shannonEntropy(m[0]), flagged: isHighEntropyToken(m[0]) })
  TOKEN_RE.lastIndex = 0
  out = out.replace(TOKEN_RE, (tok) => {
    if (isHighEntropyToken(tok)) { byDetector.entropy = (byDetector.entropy || 0) + 1; findings.push({ detector: 'entropy', len: tok.length, bits: shannonEntropy(tok) }); return REDACTED }
    return tok
  })
  return { clean: out, findings, byDetector, tokens: tokens.slice(0, 60), redactions: findings.length }
}
