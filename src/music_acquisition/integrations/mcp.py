"""MCP 2025-06-18 stdio gateway over the authenticated business REST surface.

Spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
The gateway never instantiates a HostRuntime or writes logs to stdout.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
TOOLS = {
    "music_get_capabilities": ("GET", "capabilities", False, schema({})),
    "music_sync": ("POST", "acquisition/sync", True, schema({"collectors": {"type": "array", "items": STRING}, "playlist_ids": {"type": "array", "items": STRING}, "force_refresh": {"type": "boolean"}})),
    "music_sync_status": ("GET", "sync/status", False, schema({"run_id": STRING})),
    "music_get_snapshot": ("GET", "snapshots", False, schema({"data_type": STRING, "subject_id": STRING, "local_date": STRING}, ("data_type", "subject_id"))),
    "music_recall": ("POST", "recall", False, schema({"subject_id": STRING, "query": STRING}, ("subject_id",))),
    "music_play_song": ("POST", "playback/song", False, schema({"track_id": STRING}, ("track_id",))),
    "music_play_playlist": ("POST", "playback/playlist", False, schema({"playlist_id": STRING, "track_id": STRING, "index": INTEGER}, ("playlist_id",))),
    "music_play_daily": ("POST", "playback/daily", False, schema({"date": STRING, "track_id": STRING, "index": INTEGER}, ("date",))),
    "music_next": ("POST", "playback/next", False, schema({"delta": INTEGER})),
    "music_roundtrip_lookup": ("POST", "playlist/roundtrip", True, schema({"track_id": STRING, "scope_key": STRING, "index_1based": INTEGER})),
}


class MusicToolClient:
    def __init__(self, *, base_url, token, owner_user_id="owner"):
        self.base_url, self._token, self.owner = base_url.rstrip("/"), token, owner_user_id

    def list_tools(self):
        return [{"name": name, "description": name.replace("_", " ") + "; requires the owner's prior scopes and Consent",
                 "inputSchema": spec[3]} for name, spec in TOOLS.items()]

    def call(self, name, arguments):
        if name not in TOOLS:
            raise ValueError("unknown_tool")
        method, path, body, specification = TOOLS[name]
        if not isinstance(arguments, dict) or set(arguments) - set(specification["properties"]) or set(specification["required"]) - set(arguments):
            raise ValueError("invalid_arguments")
        for key, value in arguments.items():
            kind = specification["properties"][key]["type"]
            if (kind == "string" and not isinstance(value, str)) or (kind == "integer" and type(value) is not int) or (kind == "boolean" and type(value) is not bool) or (kind == "array" and (not isinstance(value, list) or any(not isinstance(x, str) for x in value))):
                raise ValueError("invalid_argument_type")
        values = dict(arguments)
        if name == "music_sync":
            values["owner_user_id"] = self.owner
        url = self.base_url + "/api/music/v1/" + path
        payload = json.dumps(values).encode("utf-8") if body else None
        if values and not body:
            url += "?" + urllib.parse.urlencode(values)
        request = urllib.request.Request(url, data=payload, method=method, headers={"Content-Type": "application/json"})
        request.add_unredirected_header("Authorization", "Bearer " + self._token)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"HTTP_{exc.code}"}
        except (OSError, ValueError):
            return {"ok": False, "error": "SERVICE_UNAVAILABLE"}


class MusicMcpServer:
    def __init__(self, client):
        self.client = client
        self.initialized = False

    def handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        if "id" not in request:
            return None
        result, error = None, None
        method = request["method"]
        if method == "initialize":
            self.initialized = True
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "mnemosyne-music", "version": "0.5.0"}}
        elif method == "ping":
            result = {}
        elif not self.initialized:
            error = {"code": -32600, "message": "Initialize first"}
        elif method == "tools/list":
            result = {"tools": self.client.list_tools()}
        elif method == "tools/call":
            try:
                params = request.get("params") or {}
                value = self.client.call(params.get("name"), params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": value.get("ok") is False}
            except (ValueError, TypeError, AttributeError):
                error = {"code": -32602, "message": "Invalid tool or arguments"}
        else:
            error = {"code": -32601, "message": "Method not found"}
        return {"jsonrpc": "2.0", "id": request["id"], **({"error": error} if error else {"result": result})}

    def serve(self, input_stream=None, output_stream=None):
        input_stream, output_stream = input_stream or sys.stdin, output_stream or sys.stdout
        for line in input_stream:
            try:
                response = self.handle(json.loads(line))
            except ValueError:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            if response is not None:
                output_stream.write(json.dumps(response, ensure_ascii=True) + "\n")
                output_stream.flush()
