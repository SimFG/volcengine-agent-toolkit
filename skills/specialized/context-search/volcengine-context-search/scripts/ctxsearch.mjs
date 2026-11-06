#!/usr/bin/env node
/** Production Context Search OpenAPI transport. Node.js built-ins only. */
import { createHash, createHmac } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { request } from 'node:https';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const ENDPOINT = Object.freeze({
  host: 'open.volcengineapi.com', service: 'ctxsearch', version: '2025-09-01',
});
const PAIRS = [
  ['VOLCENGINE_ACCESS_KEY', 'VOLCENGINE_SECRET_KEY'],
  ['CTXSEARCH_ACCESS_KEY_ID', 'CTXSEARCH_SECRET_ACCESS_KEY'],
  ['CTXSEARCH_TEST_AK', 'CTXSEARCH_TEST_SK'],
  ['VOLC_ACCESSKEY', 'VOLC_SECRETKEY'],
];
const SESSION_TOKEN_NAMES = [
  'VOLCENGINE_SESSION_TOKEN', 'CTXSEARCH_SESSION_TOKEN', 'VOLC_SESSION_TOKEN',
];
const digest = value => createHash('sha256').update(value).digest('hex');
const mac = (key, value) => createHmac('sha256', key).update(value).digest();
const escape = value => encodeURIComponent(String(value)).replace(/[!'()*]/g,
  char => '%' + char.charCodeAt(0).toString(16).toUpperCase());
const scalar = value => ['string', 'boolean', 'number'].includes(typeof value)
  && (typeof value !== 'number' || Number.isFinite(value));

export function credentialsFrom(variables = process.env) {
  for (const [akName, skName] of PAIRS) {
    const accessKeyId = variables[akName]?.trim();
    const secretAccessKey = variables[skName]?.trim();
    if (!accessKeyId && !secretAccessKey) continue;
    if (!accessKeyId || !secretAccessKey) throw new Error(`凭据不完整：${akName} 与 ${skName} 必须同时配置。`);
    const sessionToken = SESSION_TOKEN_NAMES.map(name => variables[name]?.trim()).find(Boolean);
    return { accessKeyId, secretAccessKey, sessionToken };
  }
  throw new Error('缺少凭据：请配置 VOLCENGINE_ACCESS_KEY / VOLCENGINE_SECRET_KEY（或 CTXSEARCH_ACCESS_KEY_ID / CTXSEARCH_SECRET_ACCESS_KEY、CTXSEARCH_TEST_AK / CTXSEARCH_TEST_SK、VOLC_ACCESSKEY / VOLC_SECRETKEY）。');
}

function objectJson(text, label) {
  let value;
  try { value = JSON.parse(text); } catch { throw new Error(`${label} 必须是合法 JSON。`); }
  if (value === null || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`${label} 必须是 JSON 对象。`);
  }
  return value;
}

export function queryEntries(query) {
  const entries = [];
  for (const key of Object.keys(query).sort()) {
    if (!key || ['Action', 'Version'].includes(key)) throw new Error('业务 query 不能覆盖 Action / Version，且参数名不能为空。');
    if (query[key] == null) continue;
    const values = Array.isArray(query[key]) ? query[key] : [query[key]];
    if (!values.every(scalar)) throw new Error(`query.${key} 只接受标量或标量数组。`);
    for (const value of values.map(String).sort((a, b) => escape(a).localeCompare(escape(b), 'en', { sensitivity: 'variant' }))) {
      entries.push([key, value]);
    }
  }
  return entries;
}

export function buildSignedRequest({ action, method = 'GET', query = {}, body,
  region = 'cn-beijing', credentials, date = new Date() }) {
  if (!/^[A-Za-z][A-Za-z0-9]*$/.test(action || '')) throw new Error('必须提供合法的 --action。');
  if (!['GET', 'POST'].includes(method)) throw new Error('OpenAPI method 只接受 GET 或 POST。');
  if (method === 'GET' && body !== undefined) throw new Error('GET 请求不能包含 body；请核实接口的 method。');
  const business = queryEntries(query);
  const entries = [['Action', action], ['Version', ENDPOINT.version], ...business];
  const params = new URLSearchParams(entries);
  const canonicalQuery = entries.map(([k, v]) => [escape(k), escape(v)])
    .sort(([ak, av], [bk, bv]) => ak < bk ? -1 : ak > bk ? 1 : av < bv ? -1 : av > bv ? 1 : 0)
    .map(([k, v]) => `${k}=${v}`).join('&');
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const timestamp = date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  const headers = { 'X-Date': timestamp };
  if (credentials.sessionToken) headers['X-Security-Token'] = credentials.sessionToken;
  if (payload !== undefined) {
    headers['X-Content-Sha256'] = digest(payload);
    headers['Content-Type'] = 'application/json';
    headers['Content-Length'] = String(Buffer.byteLength(payload));
  }
  const signed = Object.entries(headers).filter(([name]) => name.startsWith('X-'))
    .map(([name, value]) => [name.toLowerCase(), value.trim().replace(/\s+/g, ' ')])
    .sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  const names = signed.map(([name]) => name).join(';');
  const canonicalHeaders = signed.map(([name, value]) => `${name}:${value}\n`).join('');
  const canonical = [method, '/', canonicalQuery, canonicalHeaders, names, digest(payload ?? '')].join('\n');
  const day = timestamp.slice(0, 8);
  const scope = `${day}/${region}/${ENDPOINT.service}/request`;
  const message = ['HMAC-SHA256', timestamp, scope, digest(canonical)].join('\n');
  const signingKey = [day, region, ENDPOINT.service, 'request']
    .reduce((key, item) => mac(key, item), credentials.secretAccessKey);
  const signature = mac(signingKey, message).toString('hex');
  headers.Authorization = `HMAC-SHA256 Credential=${credentials.accessKeyId}/${scope}, SignedHeaders=${names}, Signature=${signature}`;
  return { url: `https://${ENDPOINT.host}/?${params}`, method, headers, payload };
}

const SECRET_NAMES = /^(?:key|api.?key|access.?key(?:id)?|secret(?:access)?key|session.?token|authorization|password|app.?secret|upload.?url|object.?key|credential)$/i;
export function redact(value) {
  if (typeof value === 'string') return value.replace(/tos:\/\/[^\s"'<>]+/g, '[REDACTED]');
  if (Array.isArray(value)) return value.map(redact);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
    SECRET_NAMES.test(key) || (key === 'Value' && value.Secret === true) ? '[REDACTED]' : redact(item)]));
}
function safeMessage(error, variables = process.env) {
  let message = error instanceof Error ? error.message : String(error);
  for (const name of [...PAIRS.flat(), ...SESSION_TOKEN_NAMES]) {
    if (variables[name]) message = message.split(variables[name]).join('[REDACTED]');
  }
  return message.replace(/https?:\/\/[^\s]+/g, '[URL REDACTED]');
}

const CALL_FLAGS = new Set(['--action', '--method', '--query', '--query-file', '--param',
  '--body', '--body-file', '--region', '--timeout', '--output-file']);
const UPLOAD_FLAGS = new Set(['--url-file', '--file', '--headers-file', '--timeout']);
const SWITCHES = new Set(['--compact', '--query-stdin', '--body-stdin']);
export function parseArguments(args) {
  const words = [...args];
  const command = ['call', 'upload', 'help'].includes(words[0]) ? words.shift() : 'call';
  const options = Object.create(null);
  const allowed = command === 'upload' ? UPLOAD_FLAGS : CALL_FLAGS;
  for (let i = 0; i < words.length; i++) {
    const flag = words[i];
    if (['--help', '-h'].includes(flag)) { options.help = true; continue; }
    if (SWITCHES.has(flag) && (command !== 'upload' || flag === '--compact')) {
      if (options[flag]) throw new Error(`重复参数：${flag}`);
      options[flag] = true; continue;
    }
    if (!allowed.has(flag)) throw new Error(`未知参数：${flag}`);
    const value = words[++i];
    if (value === undefined || value.startsWith('--')) throw new Error(`${flag} 缺少值。`);
    if (flag === '--param') (options[flag] ??= []).push(value);
    else {
      if (options[flag] !== undefined) throw new Error(`重复参数：${flag}`);
      options[flag] = value;
    }
  }
  return { command, options };
}
function jsonInput(options, kind, stdin) {
  const inline = options[`--${kind}`];
  const file = options[`--${kind}-file`];
  const fromStdin = options[`--${kind}-stdin`];
  if ([inline !== undefined, file !== undefined, !!fromStdin].filter(Boolean).length > 1) {
    throw new Error(`${kind} 的 inline / file / stdin 输入互斥。`);
  }
  const raw = file !== undefined ? readFileSync(resolve(file), 'utf8')
    : fromStdin || inline === '-' ? stdin() : inline;
  return raw === undefined ? undefined : objectJson(raw, kind);
}
export function callInput(options, variables = process.env, stdin = () => readFileSync(0, 'utf8')) {
  if ((options['--query-stdin'] || options['--query'] === '-')
    && (options['--body-stdin'] || options['--body'] === '-')) throw new Error('query 与 body 不能同时读取 stdin。');
  const query = jsonInput(options, 'query', stdin) ?? {};
  for (const param of options['--param'] ?? []) {
    const at = param.indexOf('=');
    if (at < 1) throw new Error('--param 必须使用 key=value 格式。');
    Object.defineProperty(query, param.slice(0, at), { value: param.slice(at + 1), enumerable: true, configurable: true });
  }
  return buildSignedRequest({
    action: options['--action'], method: (options['--method'] ?? 'GET').toUpperCase(),
    query, body: jsonInput(options, 'body', stdin), region: options['--region'] ?? 'cn-beijing',
    credentials: credentialsFrom(variables),
  });
}

function timeoutValue(options) {
  const timeout = Number(options['--timeout'] ?? 60000);
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 600000) throw new Error('--timeout 必须为 1–600000 毫秒的整数。');
  return timeout;
}
function send({ url, method, headers, payload }, timeout) {
  return new Promise((done, fail) => {
    const target = new URL(url);
    if (target.protocol !== 'https:' || target.username || target.password) {
      fail(new Error('只允许不含用户名/密码的 HTTPS URL。')); return;
    }
    const req = request(target, { method, headers }, res => {
      const chunks = [];
      let size = 0;
      res.on('data', chunk => {
        size += chunk.length;
        if (size > 32 * 1024 * 1024) req.destroy(new Error('响应超过 32 MiB 上限。'));
        else chunks.push(chunk);
      });
      res.on('error', fail);
      res.on('aborted', () => fail(new Error('响应被中断。')));
      res.on('end', () => done({ status: res.statusCode ?? 0, text: Buffer.concat(chunks).toString('utf8') }));
    });
    const timer = setTimeout(() => req.destroy(new Error(`请求超过 ${timeout}ms。`)), timeout);
    req.on('close', () => clearTimeout(timer));
    req.on('error', fail);
    req.end(payload);
  });
}
export function classifyResponse({ status, text }, compact = false) {
  const ok = status >= 200 && status < 300;
  try {
    const value = JSON.parse(text);
    const failed = !ok || value?.ResponseMetadata?.Error != null;
    return { code: failed ? 1 : 0, text: JSON.stringify(redact(value), null, compact ? 0 : 2) + '\n' };
  } catch {
    // Non-JSON responses can be SSE or plain text; remove sensitive object paths.
    return { code: ok ? 0 : 1, text: (ok ? '' : `HTTP ${status}\n`) + redact(text) + (text.endsWith('\n') ? '' : '\n') };
  }
}

export const HELP = `Volcengine Context Search — 正式 OpenAPI（Node.js >= 18，无第三方依赖）
用法（从本 Skill 目录运行，或使用脚本绝对路径）：
  node scripts/ctxsearch.mjs [call] --action <Action> [options]
  node scripts/ctxsearch.mjs upload --url-file <path> --file <path> [options]
call:
  --action <Action>       必填；如 ListScene
  --method <GET|POST>     默认 GET
  --query <json>         JSON 对象；- 表示 stdin
  --query-file <path>    query JSON 文件
  --query-stdin          从 stdin 读取 query
  --param <key=value>    可重复；覆盖 query 中同名参数
  --body <json>          JSON 对象；- 表示 stdin
  --body-file <path>     body JSON 文件
  --body-stdin           从 stdin 读取 body
  --region <region>      默认 cn-beijing
  --timeout <ms>         默认 60000；最大 600000
  --output-file <path>   原始响应仅写入新文件，权限 0600；可能含敏感数据
  --compact             紧凑 JSON（终端输出默认脱敏）
upload:
  --url-file <path>      包含预签名 HTTPS URL 的 UTF-8 文件（不在命令行暴露 URL）
  --file <path>          待上传本地文件；固定 PUT
  --headers-file <path>  上传所需 header 的 JSON 对象
  --timeout <ms>         默认 60000
  --compact             紧凑 JSON
固定服务：ctxsearch；Host：open.volcengineapi.com；Version：2025-09-01。
凭据只从进程环境读取，按优先级依次检查：
VOLCENGINE_ACCESS_KEY/VOLCENGINE_SECRET_KEY（推荐），
CTXSEARCH_ACCESS_KEY_ID/CTXSEARCH_SECRET_ACCESS_KEY，
CTXSEARCH_TEST_AK/CTXSEARCH_TEST_SK，VOLC_ACCESSKEY/VOLC_SECRETKEY。
临时凭据按优先级读取：VOLCENGINE_SESSION_TOKEN、CTXSEARCH_SESSION_TOKEN、VOLC_SESSION_TOKEN。
退出码：0 成功；1 HTTP/API 失败；2 本地参数/凭据/网络错误。
`;
export async function main(args = process.argv.slice(2)) {
  const { command, options } = parseArguments(args);
  if (command === 'help' || options.help) { process.stdout.write(HELP); return 0; }
  const timeout = timeoutValue(options);
  if (command === 'upload') {
    if (!options['--url-file'] || !options['--file']) throw new Error('upload 需要 --url-file 与 --file。');
    const url = readFileSync(resolve(options['--url-file']), 'utf8').trim();
    const headers = options['--headers-file'] ? objectJson(readFileSync(resolve(options['--headers-file']), 'utf8'), 'headers') : {};
    if (!Object.entries(headers).every(([k, v]) => k && typeof v === 'string' && !/[\r\n]/.test(k + v))) throw new Error('上传 header 必须为无换行的字符串。');
    if (Object.keys(headers).some(k => ['authorization', 'host', 'x-security-token'].includes(k.toLowerCase()))) throw new Error('上传 header 不能携带 OpenAPI 凭据或覆盖 Host。');
    const payload = readFileSync(resolve(options['--file']));
    headers['Content-Length'] = String(payload.length);
    const res = await send({ url, method: 'PUT', headers, payload }, timeout);
    const ok = res.status >= 200 && res.status < 300;
    const line = JSON.stringify({ ok, statusCode: res.status, bytes: payload.length }) + '\n';
    (ok ? process.stdout : process.stderr).write(line);
    return ok ? 0 : 1;
  }
  const res = await send(callInput(options), timeout);
  if (options['--output-file']) writeFileSync(resolve(options['--output-file']), res.text, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
  const output = classifyResponse(res, options['--compact']);
  (output.code === 0 ? process.stdout : process.stderr).write(output.text);
  return output.code;
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().then(code => { process.exitCode = code; }).catch(error => {
    process.stderr.write(JSON.stringify({ error: safeMessage(error) }) + '\n');
    process.exitCode = 2;
  });
}
