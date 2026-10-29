"""gpt-image-2 via the Images API: prompt file in, PNG out. Logs timing and usage, never the key."""
import base64, json, os, sys, time, urllib.error, urllib.request

prompt_file, out, size, quality, background = sys.argv[1:6]
body = {"model": "gpt-image-2", "prompt": open(prompt_file).read(), "size": size, "quality": quality, "n": 1}
if background != "-":
    body["background"] = background
request = urllib.request.Request(
    "https://api.openai.com/v1/images/generations",
    data=json.dumps(body).encode(),
    headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
)
t0 = time.perf_counter()
print(f"[image] request model=gpt-image-2 size={size} quality={quality} background={background} prompt_chars={len(body['prompt'])}", flush=True)
try:
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)
except urllib.error.HTTPError as error:
    print(f"[image] HTTP {error.code} after {time.perf_counter() - t0:.1f}s: {error.read().decode()[:600]}")
    sys.exit(1)
png = base64.b64decode(data["data"][0]["b64_json"])
with open(out, "wb") as f:
    f.write(png)
print(f"[image] saved {out} ({len(png) // 1024} KB) in {time.perf_counter() - t0:.1f}s usage={data.get('usage')}")
