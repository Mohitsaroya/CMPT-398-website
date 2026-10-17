import sys, os, socket, struct, base64, threading, time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import cv2, numpy as np, requests
from ultralytics import YOLO
import queue

PORT = 5000
CONNECT_TIMEOUT = 3
RECEIVE_POLL_INTERVAL = 1
FRAME_IDLE_TIMEOUT = 10
SERVER = os.environ.get(
    "SERVER",
    "https://cmpt-398-website.onrender.com/frame"
)
TOKEN = os.environ.get("FRAME_TOKEN", "")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deck_ip.txt")

model = YOLO("best.pt")
print("classes:", model.names)

latest = None
latest_frame_number = 0
camera_frames_received = 0
lock = threading.Lock()

def rx(s, n):
    buf = bytearray()
    last_data = time.monotonic()
    while len(buf) < n:
        try:
            chunk = s.recv(n - len(buf))
        except socket.timeout:
            if time.monotonic() - last_data >= FRAME_IDLE_TIMEOUT:
                raise TimeoutError(
                    f"no deck data received for {FRAME_IDLE_TIMEOUT}s"
                )
            continue
        if not chunk: raise ConnectionError("deck closed")
        buf.extend(chunk)
        last_data = time.monotonic()
    return buf

def find_deck():
    if len(sys.argv) > 2:
        return [sys.argv[2]]

    if os.path.exists(CACHE):
        with open(CACHE) as f:
            ip = f.read().strip()
        if ip:
            return [ip]
    return []

def receive_frame(s):
    length, _, _ = struct.unpack('<HBB', rx(s, 4))
    if not 13 <= length < 64:
        raise ValueError(f"invalid deck frame header length: {length}")
    magic, w, h, depth, fmt, size = struct.unpack('<BHHBBI', rx(s, length - 2))
    if magic != 0xBC:
        raise ValueError(f"unexpected deck frame magic: {magic:#x}")

    data = bytearray()
    while len(data) < size:
        length, _, _ = struct.unpack('<HBB', rx(s, 4))
        data.extend(rx(s, length - 2))
    arr = np.frombuffer(data, np.uint8)
    if fmt == 0:
        return cv2.cvtColor(arr.reshape(h, w), cv2.COLOR_BayerBG2BGR)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)

def stream_from_deck(ip):
    global camera_frames_received, latest, latest_frame_number
    s = socket.socket()
    t0, count = time.time(), 0
    t_last = t0
    last_frame = time.time()
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(s, "ioctl") and hasattr(socket, "SIO_KEEPALIVE_VALS"):
            s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 3000, 1000))
        s.settimeout(CONNECT_TIMEOUT)
        s.connect((ip, PORT))
        s.settimeout(RECEIVE_POLL_INTERVAL)
        print(f"TCP connected to {ip}:{PORT}; waiting for first frame...")
        img = receive_frame(s)
        print("connected to deck at", ip)
        with open(CACHE, "w") as f:
            f.write(ip)

        while True:
            with lock:
                camera_frames_received += 1
                latest_frame_number = camera_frames_received
                latest = img
            count += 1
            gap = time.time() - last_frame
            last_frame = time.time()
            if gap > 0.5:
                print(f"[camera] stall: {gap:.1f}s without a frame")

            if count % 30 == 0:
                now = time.time()
                with lock:
                    received = camera_frames_received
                print(f"[camera] {30/(now - t_last):.1f} fps | "
                      f"received={received} | frame {img.shape}")
                t_last = now
            img = receive_frame(s)
    except Exception as e:
        print(f"deck error at {ip} after {time.time()-t0:.0f}s / "
              f"{count} frames: {e} - reconnecting")
    finally:
        s.close()

# ---------- frame reader (background thread) ----------
def deck_reader():
    while True:
        addresses = find_deck()
        for ip in addresses:
            stream_from_deck(ip)

        if not addresses:
            print("no deck IP configured; set server/deck_ip.txt or pass it as an argument")
        time.sleep(1)
            
def deck_frames():
    threading.Thread(target=deck_reader, daemon=True).start()
    last = None
    while True:
        with lock:
            img = latest
            frame_number = latest_frame_number
        if img is None or img is last:
            time.sleep(0.01)
            continue
        last = img
        yield img, frame_number

def local_frames(src):
    cap = cv2.VideoCapture(0 if src == "webcam" else src)
    while True:
        ok, f = cap.read()
        if not ok: break
        yield f

send_q = queue.Queue(maxsize=1)
uploader_state = threading.local()
MAX_IN_FLIGHT_UPLOADS = 3

def post_frame(payload):
    session = getattr(uploader_state, "session", None)
    if session is None:
        session = requests.Session()
        uploader_state.session = session
    response = session.post(SERVER, json=payload,
                            headers={"x-token": TOKEN}, timeout=5)
    response.raise_for_status()
    return response.status_code

def uploader():
    uploaded = 0
    superseded = 0
    window_start = time.monotonic()
    in_flight = set()

    while True:
        for future in tuple(in_flight):
            if not future.done():
                continue
            in_flight.remove(future)
            try:
                if future.result() == 204:
                    uploaded += 1
                else:
                    superseded += 1
            except requests.RequestException as e:
                print("post failed:", e)

        now = time.monotonic()
        if now - window_start >= 5:
            elapsed = now - window_start
            print(f"[uploaded] {uploaded / elapsed:.1f} fps | "
                  f"superseded {superseded}")
            uploaded = superseded = 0
            window_start = now

        if len(in_flight) >= MAX_IN_FLIGHT_UPLOADS:
            wait(in_flight, return_when=FIRST_COMPLETED)
            continue

        try:
            payload = send_q.get(timeout=0.05)
        except queue.Empty:
            continue
        in_flight.add(upload_executor.submit(post_frame, payload))

upload_executor = ThreadPoolExecutor(max_workers=MAX_IN_FLIGHT_UPLOADS)
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
    last_camera_frame_number = 0
    skipped_before_inference = 0
    frame_seq = time.time_ns() // 1000
    t_win = time.time()
    infer_t = enc_t = post_t = 0.0
    for frame in frames:
        if src == "deck":
            img, camera_frame_number = frame
            skipped_before_inference += max(
                0, camera_frame_number - last_camera_frame_number - 1
            )
            last_camera_frame_number = camera_frame_number
        else:
            img = frame

        a = time.perf_counter()
        r = model(img, conf=0.25, verbose=False)[0]
        dets = [{"label": model.names[int(b.cls)],
                 "conf": float(b.conf),
                 "box": [float(v) for v in b.xyxy[0]]} for b in r.boxes]
        b_ = time.perf_counter()

        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        frame_seq += 1
        payload = {"image": base64.b64encode(jpg).decode(),
                   "detections": dets, "frameSeq": frame_seq}
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
            stage_counts = ""
            if src == "deck":
                with lock:
                    received = camera_frames_received
                stage_counts = (
                    f" | camera received={received}, processed={n}, "
                    f"skipped_before_inference={skipped_before_inference}"
                )
            print(f"[processed] {fps:.1f} fps{stage_counts} | per frame: "
                  f"infer {infer_t/WINDOW*1000:.0f} ms, "
                  f"encode {enc_t/WINDOW*1000:.0f} ms, "
                  f"post {post_t/WINDOW*1000:.0f} ms")
            t_win = now
            infer_t = enc_t = post_t = 0.0
    
main()