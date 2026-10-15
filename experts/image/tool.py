from typing import Any
import base64
import binascii
import json
import os
import re
import time
import urllib.error
import urllib.request

from noah import filesvc

# Qwen-Image-2.1 wants sides divisible by 32. Its native presets are about 2048 px, which takes minutes per step on a small GPU,
# so these stay near one megapixel.
SIZES = {"square": "1024x1024", "landscape": "1344x768", "portrait": "768x1344"}
TIMEOUT = int(os.environ.get("NOAH_IMAGE_TIMEOUT", "3600"))  # seconds; the first request also loads the models, and an old GPU needs minutes per step
SETUP = "Start it with scripts/image_server.sh (see \"Image expert\" in the README), or set NOAH_IMAGE_URL."

_LEAD = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:draw|generate|create|make|paint|render|design|sketch|illustrate)"
                   r"(?:\s+me)?(?:\s+(?:an?|the|some))?(?:\s+(?:image|picture|photo|drawing|illustration|painting)s?)?(?:\s+(?:of|showing))?\s+",
                   re.I)


def image_url() -> str:
    return os.environ.get("NOAH_IMAGE_URL", "http://localhost:1234").rstrip("/")


def extract_rule(message: str) -> dict[str, Any]:
    low = message.lower()
    shape = ("landscape" if re.search(r"\b(landscape|wide|widescreen|wallpaper|banner|16:9)\b", low)
             else "portrait" if re.search(r"\b(portrait|tall|vertical|phone wallpaper|9:16)\b", low) else "square")
    prompt = _LEAD.sub("", message.strip(), count=1).strip() or message.strip()
    return {"prompt": prompt, "shape": shape}


def _file_name(prompt: str) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", prompt.lower())[:6])[:48] or "image"
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.png"


def generate(prompt: str, size: str) -> bytes:
    """One PNG from sd-server's OpenAI-style images endpoint."""
    req = urllib.request.Request(image_url() + "/v1/images/generations", method="POST",
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps({"prompt": prompt, "n": 1, "size": size, "output_format": "png"}).encode())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body: dict[str, Any] = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"The image server refused the request (HTTP {e.code}): {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"No image server is running at {image_url()} ({e.reason}). {SETUP}") from None
    except ConnectionError:
        raise RuntimeError("The image server stopped before sending the picture (it may have run out of memory; see its log).") from None
    except TimeoutError:
        raise RuntimeError(f"The image server took longer than {TIMEOUT // 60} minutes.") from None
    try:
        return base64.b64decode(body["data"][0]["b64_json"], validate=True)
    except (KeyError, IndexError, TypeError, binascii.Error):
        raise RuntimeError("The image server answered without an image.") from None


def run(args: dict[str, Any]) -> dict[str, Any]:
    prompt = str(args["prompt"]).strip()
    shape = str(args.get("shape") or "square")
    size = SIZES.get(shape, SIZES["square"])
    png = generate(prompt, size)
    if not png.startswith(b"\x89PNG"):
        raise RuntimeError("The image server sent something that is not a PNG.")
    folder = filesvc.files_root() / "generated"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _file_name(prompt)
    path.write_bytes(png)
    info = filesvc.describe(path)
    info["preview"] = filesvc.preview(path)
    return {"text": f"Here is your picture. It is saved as {info['path']} in your files.", "files": [info]}
