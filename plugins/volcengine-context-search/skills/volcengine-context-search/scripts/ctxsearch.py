#!/usr/bin/env python3
"""Production Context Search OpenAPI transport; Python 3.9+ standard library only."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from types import MappingProxyType
from urllib.parse import quote, urlencode, urlsplit

ENDPOINT = MappingProxyType({
    "host": "open.volcengineapi.com", "service": "ctxsearch", "version": "2025-09-01",
})
CREDENTIAL_PAIRS = (
    ("VOLCENGINE_ACCESS_KEY", "VOLCENGINE_SECRET_KEY"),
    ("CTXSEARCH_ACCESS_KEY_ID", "CTXSEARCH_SECRET_ACCESS_KEY"),
    ("CTXSEARCH_TEST_AK", "CTXSEARCH_TEST_SK"),
    ("VOLC_ACCESSKEY", "VOLC_SECRETKEY"),
)
SESSION_TOKEN_NAMES = (
    "VOLCENGINE_SESSION_TOKEN", "CTXSEARCH_SESSION_TOKEN", "VOLC_SESSION_TOKEN",
)
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
REDACTED = "[REDACTED]"


def credentials_from(variables=None):
    variables = os.environ if variables is None else variables
    for ak_name, sk_name in CREDENTIAL_PAIRS:
        ak = variables.get(ak_name, "").strip()
        sk = variables.get(sk_name, "").strip()
        if not ak and not sk:
            continue
        if not ak or not sk:
            raise ValueError(f"凭据不完整：{ak_name} 与 {sk_name} 必须同时配置。")
        token = next((variables.get(name, "").strip() for name in SESSION_TOKEN_NAMES
                      if variables.get(name, "").strip()), None)
        return {"access_key_id": ak, "secret_access_key": sk, "session_token": token}
    raise ValueError("缺少凭据：请配置 VOLCENGINE_ACCESS_KEY / VOLCENGINE_SECRET_KEY"
                     "（或 CTXSEARCH_ACCESS_KEY_ID / CTXSEARCH_SECRET_ACCESS_KEY、"
                     "CTXSEARCH_TEST_AK / CTXSEARCH_TEST_SK、VOLC_ACCESSKEY / VOLC_SECRETKEY）。")


def json_text(value, compact=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":") if compact else None,
                      indent=None if compact else 2)


def object_json(text, label):
    def invalid_constant(_):
        raise ValueError("non-finite JSON")
    try:
        value = json.loads(text, parse_constant=invalid_constant)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} 必须是合法 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON 对象。")
    return value


def scalar_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("query 不接受非有限数值。")
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    raise ValueError("query 只接受标量或标量数组。")


def query_entries(query):
    if not isinstance(query, dict):
        raise ValueError("query 必须是对象。")
    entries = []
    for key in sorted(query):
        if not isinstance(key, str) or not key or key in ("Action", "Version"):
            raise ValueError("业务 query 不能覆盖 Action / Version，且参数名不能为空。")
        value = query[key]
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        values = sorted((scalar_text(item) for item in values), key=lambda item: quote(item, safe="-_.~"))
        entries.extend((key, item) for item in values)
    return entries


def build_signed_request(*, action, credentials, method="GET", query=None, body=None,
                         region="cn-beijing", date=None):
    if not isinstance(action, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", action):
        raise ValueError("必须提供合法的 --action。")
    if method not in ("GET", "POST"):
        raise ValueError("OpenAPI method 只接受 GET 或 POST。")
    if body is not None and not isinstance(body, dict):
        raise ValueError("body 必须是 JSON 对象。")
    if method == "GET" and body is not None:
        raise ValueError("GET 请求不能包含 body；请核实接口的 method。")
    if not isinstance(region, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", region):
        raise ValueError("--region 必须是合法地域名称。")
    entries = [("Action", action), ("Version", ENDPOINT["version"])]
    entries.extend(query_entries({} if query is None else query))
    canonical_query = "&".join(f"{key}={value}" for key, value in sorted(
        (quote(key, safe="-_.~"), quote(value, safe="-_.~")) for key, value in entries))
    payload = None if body is None else json_text(body, compact=True).encode("utf-8")
    date = datetime.now(timezone.utc) if date is None else date
    if date.tzinfo is None:
        raise ValueError("签名日期必须包含时区。")
    timestamp = date.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers = {"X-Date": timestamp}
    if credentials.get("session_token"):
        headers["X-Security-Token"] = credentials["session_token"]
    if payload is not None:
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(payload)),
                        "X-Content-Sha256": hashlib.sha256(payload).hexdigest()})
    signed = sorted((key.lower(), " ".join(value.split())) for key, value in headers.items()
                    if key.startswith("X-"))
    names = ";".join(key for key, _ in signed)
    canonical_headers = "".join(f"{key}:{value}\n" for key, value in signed)
    canonical = "\n".join((method, "/", canonical_query, canonical_headers, names,
                           hashlib.sha256(payload or b"").hexdigest()))
    day = timestamp[:8]
    scope = f"{day}/{region}/{ENDPOINT['service']}/request"
    message = "\n".join(("HMAC-SHA256", timestamp, scope,
                          hashlib.sha256(canonical.encode()).hexdigest()))
    signing_key = credentials["secret_access_key"].encode("utf-8")
    for part in (day, region, ENDPOINT["service"], "request"):
        signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing_key, message.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"HMAC-SHA256 Credential={credentials['access_key_id']}/{scope}, "
        f"SignedHeaders={names}, Signature={signature}")
    return {"url": f"https://{ENDPOINT['host']}/?{urlencode(entries)}", "method": method,
            "headers": headers, "payload": payload}


SECRET_NAMES = re.compile(
    r"^(?:key|api.?key|access.?key(?:id)?|secret(?:access)?key|session.?token|authorization|"
    r"password|app.?secret|upload.?url|object.?key|credential|"
    r"VOLCENGINE_(?:ACCESS_KEY|SECRET_KEY|SESSION_TOKEN))$", re.I)
# Structured JSON is redacted recursively. These patterns additionally cover labelled
# free text and SSE fragments without printing configured credential values.
TEXT_ASSIGNMENT = re.compile(
    r'''(?ix)(\b(?:api[_-]?key|access[_-]?key(?:id)?|secret[_-]?(?:access[_-]?)?key|
    session[_-]?token|password|app[_-]?secret|upload[_-]?url|object[_-]?key|
    VOLCENGINE_(?:ACCESS_KEY|SECRET_KEY|SESSION_TOKEN))\b["']?\s*[:=]\s*)
    (?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;\}\]\r\n]+)''')


def redact_text(text, variables=None):
    variables = os.environ if variables is None else variables
    values = {variables.get(name, "").strip() for name in
              [item for pair in CREDENTIAL_PAIRS for item in pair] + list(SESSION_TOKEN_NAMES)}
    for value in sorted(values - {""}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    text = re.sub(r"tos://[^\s\"'<>]+", REDACTED, text)
    text = re.sub(r"https?://[^\s\"'<>]*[?&](?:X-Amz-|X-Signature|Signature=|token=)[^\s\"'<>]*",
                  REDACTED, text, flags=re.I)
    text = re.sub(r'''(?im)(\bauthorization["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]+)''',
                  lambda match: match.group(1) + REDACTED, text)
    return TEXT_ASSIGNMENT.sub(lambda match: match.group(1) + REDACTED, text)


def redact(value, variables=None):
    if isinstance(value, str):
        return redact_text(value, variables)
    if isinstance(value, list):
        return [redact(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: REDACTED if SECRET_NAMES.fullmatch(key)
                or (key == "Value" and value.get("Secret") is True)
                else redact(item, variables) for key, item in value.items()}
    return value


def safe_message(error, variables=None):
    return re.sub(r"https?://[^\s]+", "[URL REDACTED]", redact_text(str(error), variables))


CALL_FLAGS = {"--action", "--method", "--query", "--query-file", "--param", "--body",
              "--body-file", "--region", "--timeout", "--output-file"}
UPLOAD_FLAGS = {"--url-file", "--file", "--headers-file", "--timeout"}
SWITCHES = {"--compact", "--query-stdin", "--body-stdin"}


def parse_arguments(argv):
    words = list(argv)
    command = words.pop(0) if words and words[0] in ("call", "upload", "help") else "call"
    options = {}
    allowed = UPLOAD_FLAGS if command == "upload" else CALL_FLAGS
    index = 0
    while index < len(words):
        flag = words[index]
        index += 1
        if flag in ("--help", "-h"):
            options["help"] = True
            continue
        if flag in SWITCHES and (command != "upload" or flag == "--compact"):
            if flag in options:
                raise ValueError(f"重复参数：{flag}")
            options[flag] = True
            continue
        if flag not in allowed:
            raise ValueError(f"未知参数：{flag}")
        if index == len(words) or words[index].startswith("--"):
            raise ValueError(f"{flag} 缺少值。")
        value = words[index]
        index += 1
        if flag == "--param":
            options.setdefault(flag, []).append(value)
        elif flag in options:
            raise ValueError(f"重复参数：{flag}")
        else:
            options[flag] = value
    return command, options


def json_input(options, kind, stdin):
    inline = options.get(f"--{kind}")
    file = options.get(f"--{kind}-file")
    from_stdin = options.get(f"--{kind}-stdin", False)
    if sum((inline is not None, file is not None, bool(from_stdin))) > 1:
        raise ValueError(f"{kind} 的 inline / file / stdin 输入互斥。")
    if file is not None:
        raw = Path(file).read_text(encoding="utf-8")
    elif from_stdin or inline == "-":
        raw = stdin()
    else:
        raw = inline
    return None if raw is None else object_json(raw, kind)


def call_input(options, variables=None, stdin=None):
    stdin = sys.stdin.read if stdin is None else stdin
    if (options.get("--query-stdin") or options.get("--query") == "-") and (
            options.get("--body-stdin") or options.get("--body") == "-"):
        raise ValueError("query 与 body 不能同时读取 stdin。")
    query = json_input(options, "query", stdin) or {}
    for param in options.get("--param", []):
        key, separator, value = param.partition("=")
        if not key or not separator:
            raise ValueError("--param 必须使用 key=value 格式。")
        query[key] = value
    return build_signed_request(action=options.get("--action"),
                                method=options.get("--method", "GET").upper(), query=query,
                                body=json_input(options, "body", stdin),
                                region=options.get("--region", "cn-beijing"),
                                credentials=credentials_from(variables))


def timeout_value(options):
    try:
        timeout = int(options.get("--timeout", "60000"))
    except (ValueError, TypeError) as exc:
        raise ValueError("--timeout 必须为 1–600000 毫秒的整数。") from exc
    if not 1 <= timeout <= 600000:
        raise ValueError("--timeout 必须为 1–600000 毫秒的整数。")
    return timeout


def send(request, timeout):
    target = urlsplit(request["url"])
    if target.scheme != "https" or not target.hostname or target.username or target.password:
        raise ValueError("只允许不含用户名/密码的 HTTPS URL。")
    deadline = time.monotonic() + timeout / 1000
    connection = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=timeout / 1000)
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"请求超过 {timeout}ms。")
        return left
    def set_socket_timeout():
        if connection.sock is not None:
            connection.sock.settimeout(remaining())
    try:
        connection.connect()
        set_socket_timeout()
        path = target.path or "/"
        if target.query:
            path += "?" + target.query
        connection.request(request["method"], path, body=request.get("payload"), headers=request["headers"])
        set_socket_timeout()
        response = connection.getresponse()
        chunks = []
        size = 0
        while True:
            set_socket_timeout()
            remaining()
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("响应超过 32 MiB 上限。")
            chunks.append(chunk)
        return {"status": response.status, "text": b"".join(chunks).decode("utf-8", errors="replace")}
    finally:
        connection.close()


def classify_response(response, compact=False, variables=None):
    ok = 200 <= response["status"] < 300
    text = response["text"]
    try:
        value = json.loads(text)
    except ValueError:
        # SSE data objects need structural redaction too, especially Secret variables.
        lines = []
        for line in text.splitlines(keepends=True):
            prefix = "data:" if line.startswith("data:") else ""
            candidate = line[len(prefix):].strip()
            try:
                parsed = json.loads(candidate)
            except ValueError:
                lines.append(redact_text(line, variables))
            else:
                suffix = "\n" if line.endswith("\n") else ""
                lines.append(prefix + (" " if prefix else "") + json_text(redact(parsed, variables), True) + suffix)
        cleaned = "".join(lines)
        return {"code": 0 if ok else 1,
                "text": ("" if ok else f"HTTP {response['status']}\n") + cleaned
                + ("" if cleaned.endswith("\n") else "\n")}
    metadata = value.get("ResponseMetadata", {}) if isinstance(value, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    failed = not ok or metadata.get("Error") is not None
    return {"code": 1 if failed else 0,
            "text": json_text(redact(value, variables), compact) + "\n"}


def write_private_new_file(path, text):
    # O_EXCL prevents overwriting user files and following an existing symlink.
    fd = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def upload_input(options):
    if not options.get("--url-file") or not options.get("--file"):
        raise ValueError("upload 需要 --url-file 与 --file。")
    url = Path(options["--url-file"]).read_text(encoding="utf-8").strip()
    headers = object_json(Path(options["--headers-file"]).read_text(encoding="utf-8"), "headers") \
        if options.get("--headers-file") else {}
    for key, value in headers.items():
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError("上传 header 必须为合法名称和无换行的字符串。")
        if key.lower() in ("authorization", "host", "x-security-token"):
            raise ValueError("上传 header 不能携带 OpenAPI 凭据或覆盖 Host。")
    payload = Path(options["--file"]).read_bytes()
    headers = {key: value for key, value in headers.items() if key.lower() != "content-length"}
    headers["Content-Length"] = str(len(payload))
    return {"url": url, "method": "PUT", "headers": headers, "payload": payload}


HELP = """Volcengine Context Search — 正式 OpenAPI（Python >= 3.9，无第三方依赖）
用法（从本 Skill 目录运行，或使用脚本绝对路径）：
  python3 scripts/ctxsearch.py [call] --action <Action> [options]
  python3 scripts/ctxsearch.py upload --url-file <path> --file <path> [options]
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
  --url-file <path>      预签名 HTTPS URL 的 UTF-8 文件
  --file <path>          待上传本地文件；固定 PUT
  --headers-file <path>  上传所需 header 的 JSON 对象
  --timeout <ms>         默认 60000
  --compact             紧凑 JSON
固定服务：ctxsearch；Host：open.volcengineapi.com；Version：2025-09-01。
凭据优先级：VOLCENGINE_ACCESS_KEY/VOLCENGINE_SECRET_KEY，
CTXSEARCH_ACCESS_KEY_ID/CTXSEARCH_SECRET_ACCESS_KEY，
CTXSEARCH_TEST_AK/CTXSEARCH_TEST_SK，VOLC_ACCESSKEY/VOLC_SECRETKEY。
临时凭据优先级：VOLCENGINE_SESSION_TOKEN、CTXSEARCH_SESSION_TOKEN、VOLC_SESSION_TOKEN。
退出码：0 成功；1 HTTP/API 失败；2 本地参数/凭据/网络错误。
"""


def main(argv=None):
    command, options = parse_arguments(sys.argv[1:] if argv is None else argv)
    if command == "help" or options.get("help"):
        sys.stdout.write(HELP)
        return 0
    timeout = timeout_value(options)
    if command == "upload":
        request = upload_input(options)
        response = send(request, timeout)
        ok = 200 <= response["status"] < 300
        (sys.stdout if ok else sys.stderr).write(json_text({"ok": ok, "statusCode": response["status"],
                                                          "bytes": len(request["payload"])}, True) + "\n")
        return 0 if ok else 1
    response = send(call_input(options), timeout)
    if options.get("--output-file"):
        write_private_new_file(options["--output-file"], response["text"])
    output = classify_response(response, options.get("--compact", False))
    (sys.stdout if output["code"] == 0 else sys.stderr).write(output["text"])
    return output["code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        sys.stderr.write(json_text({"error": safe_message(error)}, True) + "\n")
        sys.exit(2)
