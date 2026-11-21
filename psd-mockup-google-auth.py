"""One-time Google Drive sign-in, run on the owner's Mac (not in the cloud).

Reads the newest ~/Downloads/client_secret_*.json (Desktop OAuth client),
opens the browser for consent (full Drive scope, offline access, PKCE),
catches the reply on a 127.0.0.1 loopback port, writes ONLY the refresh
token and client ID to ~/psd-mockup-google-oauth.json (mode 600), and uploads
that file to My Drive/PSD Mockup Generator/auth/ (folder created if missing).
Prints no secret values.
Usage: python3 google_auth_setup.py"""
import base64, glob, hashlib, http.server, json, os, secrets, urllib.parse, urllib.request, webbrowser

files = sorted(glob.glob(os.path.expanduser("~/Downloads/client_secret_*.json")), key=os.path.getmtime)
if not files:
    raise SystemExit("No ~/Downloads/client_secret_*.json found. Download the Desktop client JSON first.")
client = json.load(open(files[-1]))["installed"]

verifier = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
reply = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q or "error" in q:
            reply.update(q)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Done. You can close this tab and go back to Claude.")

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
redirect = f"http://127.0.0.1:{server.server_port}"
url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
    "scope": "https://www.googleapis.com/auth/drive", "access_type": "offline", "prompt": "consent",
    "code_challenge": challenge, "code_challenge_method": "S256"})
print("Opening the browser for Google sign-in...")
webbrowser.open(url)
while not reply:
    server.handle_request()
if "code" not in reply:
    raise SystemExit("Google returned an error: " + str(reply.get("error")))

data = urllib.parse.urlencode({
    "code": reply["code"][0], "client_id": client["client_id"], "client_secret": client["client_secret"],
    "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier}).encode()
token = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data))
if not token.get("refresh_token"):
    raise SystemExit("No refresh token returned (keys: %s)" % sorted(token))

out = os.path.expanduser("~/psd-mockup-google-oauth.json")
os.umask(0o077)
json.dump({"refresh_token": token["refresh_token"], "client_id": client["client_id"], "scope": token.get("scope")}, open(out, "w"))
os.chmod(out, 0o600)
print("refresh_token present: True | scope:", token.get("scope"), "| saved to", out)

# Upload the token file to My Drive/PSD Mockup Generator/auth/ with the fresh access token.
API = "https://www.googleapis.com/drive/v3/files"
auth = {"Authorization": "Bearer " + token["access_token"]}


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers={**auth, **(headers or {})}, method=method)
    return json.load(urllib.request.urlopen(req))


def find_folder(name, parent):
    q = f"name = '{name}' and '{parent}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    hits = call(API + "?" + urllib.parse.urlencode({"q": q, "fields": "files(id,name)"}))["files"]
    return hits[0]["id"] if hits else None


def make_folder(name, parent):
    body = json.dumps({"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent]}).encode()
    return call(API + "?fields=id", body, {"Content-Type": "application/json"}, "POST")["id"]


project = find_folder("PSD Mockup Generator", "root") or make_folder("PSD Mockup Generator", "root")
auth_dir = find_folder("auth", project) or make_folder("auth", project)
boundary = "psdmockup" + secrets.token_hex(8)
meta = json.dumps({"name": "google_oauth.json", "parents": [auth_dir]})
body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n").encode() + open(out, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
up = call("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name", body,
          {"Content-Type": f"multipart/related; boundary={boundary}"}, "POST")
print("Uploaded to Drive: My Drive/PSD Mockup Generator/auth/" + up["name"])
print("ALL DONE - you can go back to Claude.")

