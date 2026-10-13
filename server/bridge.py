import sys, os, socket, struct, base64, threading, time
from concurrent.futures import ThreadPoolExecutor
import cv2, numpy as np, requests
from ultralytics import YOLO
import queue

PORT = 5000
SERVER = os.environ.get(
    "SERVER",
    "https://cmpt-398-website.onrender.com/frame"
)
TOKEN = os.environ.get("FRAME_TOKEN", "")
FALLBACKS = ["192.168.4.1"]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deck_ip.txt")

model = YOLO("best.pt")
print("classes:", model.names)

latest = None
lock = threading.Lock()

def rx(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk: raise ConnectionError("deck closed")
        buf.extend(chunk)
    return buf

# ---------- deck discovery ----------
def port_open(ip, timeout=0.5):
    try:
        with socket.create_connection((ip, PORT), timeout=timeout):
            return True
    except OSError:
        return False

def my_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()

def scan_subnet():
    me = my_ip()
    if not me: return []
    prefix = me.rsplit(".", 1)[0]
    ips = [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != me]
    with ThreadPoolExecutor(64) as ex:
        res = list(ex.map(lambda ip: ip if port_open(ip, 0.5) else None, ips))
    return [ip for ip in res if ip]

def looks_like_deck(ip):
    """Confirm the host sends the AI Deck frame header (magic byte 0xBC)."""
    try:
        with socket.create_connection((ip, PORT), timeout=3) as s:
            s.settimeout(3)
            length, _, _ = struct.unpack('<HBB', rx(s, 4))
            if not 2 < length < 64: return False
            return rx(s, length - 2)[0] == 0xBC
    except Exception:
        return False

def find_deck():
    known = []
    if len(sys.argv) > 2: known.append(sys.argv[2])
    if os.path.exists(CACHE):
        known.append(open(CACHE).read().strip())
    for ip in known + FALLBACKS:
        if ip:
            return ip
    print("deck not at known addresses, scanning subnet...")
    found = scan_subnet()
    if len(found) == 1:
        return found[0]
    return next((ip for ip in found if looks_like_deck(ip)), None)

# ---------- frame reader (background thread) ----------
def deck_reader():
    global latest
    while True:
        ip = find_deck()
        if not ip:
            print("no deck found, retrying in 3s (is it powered and on Wi-Fi?)")
            time.sleep(3)
            continue
        s = socket.socket()
        t0, count = time.time(), 0
        t_last = t0
        last_frame = time.time()
        try:
            s.settimeout(10)
            s.connect((ip, PORT))
            print("connected to deck at", ip)
            with open(CACHE, "w") as f: f.write(ip)
            while True:
                length, _, _ = struct.unpack('<HBB', rx(s, 4))
                magic, w, h, depth, fmt, size = struct.unpack('<BHHBBI', rx(s, length - 2))
                if magic != 0xBC: continue
                data = bytearray()
                while len(data) < size:
                    length, _, _ = struct.unpack('<HBB', rx(s, 4))
                    data.extend(rx(s, length - 2))
                arr = np.frombuffer(data, np.uint8)
                if fmt == 0:
                    img = cv2.cvtColor(arr.reshape(h, w), cv2.COLOR_BayerBG2BGR)
                else:
                    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                with lock:
                    latest = img
                count += 1
                gap = time.time() - last_frame
                last_frame = time.time()
                if gap > 0.5:
                    print(f"[camera] stall: {gap:.1f}s without a frame")

                if count % 30 == 0:
                    now = time.time()
                    print(f"[camera] {30/(now - t_last):.1f} fps | frame {img.shape}")
                    t_last = now

        except Exception as e:
            print(f"deck error after {time.time()-t0:.0f}s / {count} frames: {e} - reconnecting")
            s.close()
            time.sleep(1)
            
def deck_frames():
    threading.Thread(target=deck_reader, daemon=True).start()
    last = None
    while True:
        with lock:
            img = latest
        if img is None or img is last:
            time.sleep(0.01)
            continue
        last = img
        yield img

def local_frames(src):
    cap = cv2.VideoCapture(0 if src == "webcam" else src)
    while True:
        ok, f = cap.read()
        if not ok: break
        yield f

session = requests.Session()
send_q = queue.Queue(maxsize=1)

def uploader():
    uploaded = 0
    window_start = time.monotonic()
    while True:
        payload = send_q.get()
        try:
            response = session.post(SERVER, json=payload,
                                    headers={"x-token": TOKEN}, timeout=5)
            response.raise_for_status()
            uploaded += 1
            now = time.monotonic()
            if now - window_start >= 5:
                print(f"[uploaded] {uploaded / (now - window_start):.1f} fps")
                uploaded = 0
                window_start = now
        except requests.RequestException as e:
            print("post failed:", e)

threading.Thread(target=uploader, daemon=True).start()

def enqueue(payload):
    try:
        send_q.put_nowait(payload)
    except queue.Full:
        try: send_q.get_nowait()
        except queue.Empty: pass
        send_q.put_nowait(payload)

def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "deck"
    frames = deck_frames() if src == "deck" else local_frames(src)

    WINDOW = 30
    n = 0
    t_win = time.time()
    infer_t = enc_t = post_t = 0.0
    for img in frames:
        a = time.perf_counter()
        r = model(img, conf=0.25, verbose=False)[0]
        dets = [{"label": model.names[int(b.cls)],
                 "conf": float(b.conf),
                 "box": [float(v) for v in b.xyxy[0]]} for b in r.boxes]
        b_ = time.perf_counter()

        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        payload = {"image": base64.b64encode(jpg).decode(), "detections": dets}
        c = time.perf_counter()

        enqueue(payload)
        d = time.perf_counter()

        infer_t += b_ - a
        enc_t += c - b_
        post_t += d - c
        n += 1

        if n % WINDOW == 0:
            now = time.time()
            fps = WINDOW / (now - t_win)
            print(f"[processed] {fps:.1f} fps | per frame: "
                  f"infer {infer_t/WINDOW*1000:.0f} ms, "
                  f"encode {enc_t/WINDOW*1000:.0f} ms, "
                  f"post {post_t/WINDOW*1000:.0f} ms")
            t_win = now
            infer_t = enc_t = post_t = 0.0
    
main()